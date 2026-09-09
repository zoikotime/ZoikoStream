import base64
import html
import logging
from datetime import datetime, timezone
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
# Developer-platform identity (ZST-EC-001 DEV-002). Same authenticated address, different
# approved display name - a developer receiving credential mail should see it come from the
# platform surface they issued the credential on, not from generic security mail.
SENDER_DEVELOPER = "Zoiko Steam Developer Platform"


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


# ── IDN-002 Account ready ───────────────────────────────────────────────────────────────
# Class C (Transactional) · Sender: Zoiko Steam · Recipient: account holder.
# Trigger: identity verified AND access activated — never registration.
#
# This replaces the former "Welcome to ZoikoStream" template, which fired at registration
# and asserted "Your account is active and ready to go" against an address nobody had
# verified. Corrected in place rather than duplicated so the wrong copy cannot be reached.
#
# Sender is Zoiko Steam, NOT Zoiko Steam Security: this is Class C, and routing routine
# transactional mail through the security identity dilutes the signal that identity
# carries for genuine Class A messages.
#
# The body's closing sentence is load-bearing. It scopes the claim to "the access granted
# to you" instead of promising unrestricted production access, which matters because this
# platform has no MFA, policy-acceptance or step-up subsystem to qualify it further.

IDN_002_SUBJECT = "Your Zoiko Steam access is ready"
IDN_002_PREHEADER = "Sign in and complete your account setup."
IDN_002_HEADLINE = "Welcome to Zoiko Steam."
IDN_002_BODY = (
    "Your identity has been verified and your Zoiko Steam access is active. Available "
    "Organizations, workspaces, roles, and modes are determined by the access granted to you."
)
IDN_002_CTA = "Open Zoiko Steam"

SENDER_DEFAULT = "Zoiko Steam"


def account_ready_url() -> str:
    """Authenticated entry point for IDN-002's CTA.

    `/login` is the real route (client App.jsx) and is the correct destination: the account
    is verified but not signed in, and the message must not carry anything that would sign
    them in. No token, no identifier, no query string at all.
    """
    return f"{public_base_url()}/login"


# --- IDN-003 / IDN-004 / IDN-005 - Identity security -----------------------------------
# All three are Class A (Security), sender Zoiko Steam Security, recipient the account
# holder. They are mandatory: no preference, marketing or organization setting may suppress
# them, and nothing in this module consults one.
#
# Shared controls applied to all three:
#   * HTML and plain text, always
#   * no tracking pixel and no remote asset (the only image is the inline cid logo)
#   * no secrets: no password, hash, OTP, JWT, access/refresh token, session secret or
#     verification token is passed into or rendered by any of them
#   * timestamps are rendered by the caller as timezone-aware UTC strings
#   * approximate location is always LABELLED approximate, and says so when unavailable
#   * device is a normalized family string ("Chrome on Windows"), never a raw User-Agent
#   * detection logic and thresholds are never disclosed (IDN-004 in particular)


def security_url() -> str:
    """CTA target for all three security templates.

    There is no dedicated Account Security page in this product. The closest real surface is
    the organization Settings security tab, which is deep-linkable and already renders the
    account's security posture. `tab` is a UI selector, not sensitive data, so it is safe in
    the query string. Reported as a gap: IDN-003/004/005 all want a per-account security and
    recovery surface that does not exist yet.
    """
    return public_base_url() + "/organization/settings?tab=security"


def _security_rows_html(rows):
    return "".join(
        '<tr><td style="padding:10px 0;color:#4a4a5a;font-size:14px;">'
        + html.escape(k)
        + '</td><td style="padding:10px 0;text-align:right;font-size:14px;">'
        + html.escape(v)
        + "</td></tr>"
        for k, v in rows
    )


def _security_shell(header_title, headline, preheader, rows, body, cta_label, cta_url,
                    footer_note):
    """Shared Class A layout. One dominant action, one facts table, one recovery note."""
    return _shell(f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;">{html.escape(preheader)}</div>
    {_header(header_title)}
    <div style="padding:24px 32px 40px;color:#333;font-size:16px;line-height:1.6;">
      <h2 style="margin:0 0 16px;font-size:18px;font-weight:600;color:#2e2e4d;">{html.escape(headline)}</h2>
      <p style="margin:0 0 20px;">{body}</p>
      <table style="width:100%;border-collapse:collapse;margin:0 0 24px;">{_security_rows_html(rows)}</table>
      <p style="text-align:center;margin:32px 0;">
        <a href="{html.escape(cta_url, quote=True)}"
           style="background:#2e2e4d;color:#fff;text-decoration:none;padding:16px 32px;
           border-radius:4px;font-weight:bold;font-size:16px;display:inline-block;">
          {html.escape(cta_label)}
        </a>
      </p>
      <p style="margin:0;color:#4a4a5a;font-size:14px;">{footer_note}</p>
    </div>""")


def _security_text(headline, preheader, rows, body, cta_label, cta_url, footer_note):
    lines = [headline, "", body, "", preheader, ""]
    lines += [f"{k}: {v}" for k, v in rows]
    lines += ["", f"{cta_label}: {cta_url}", "", footer_note, "", "Zoiko Steam Security"]
    return chr(10).join(lines)


# --- IDN-003 New sign-in detected ------------------------------------------------------

IDN_003_SUBJECT = "New sign-in to your Zoiko Steam account"
IDN_003_PREHEADER = "Review the time, device, and approximate location."
IDN_003_HEADLINE = "A new sign-in was recorded."
IDN_003_CTA = "Review account security"
IDN_003_RECOVERY = (
    "If this was not you, review your account security now and change your password "
    "immediately."
)


def send_new_sign_in_email(to, *, signed_in_at, authentication_method, device, location,
                           session_reference):
    """IDN-003. `location` is already the display string, including the unavailable case."""
    body = (
        "Your account was used to sign in to Zoiko Steam at "
        + html.escape(signed_in_at)
        + " using "
        + html.escape(authentication_method)
        + ". Review the approximate location, device, browser, and session reference."
    )
    text_body = (
        f"Your account was used to sign in to Zoiko Steam at {signed_in_at} using "
        f"{authentication_method}. Review the approximate location, device, browser, "
        "and session reference."
    )
    rows = [
        ("Signed in at", signed_in_at),
        ("Device", device),
        ("Approximate location", location),
        ("Session reference", session_reference),
    ]
    url = security_url()
    return _send(
        to,
        IDN_003_SUBJECT,
        _security_shell("New sign-in detected", IDN_003_HEADLINE, IDN_003_PREHEADER, rows,
                        body, IDN_003_CTA, url, IDN_003_RECOVERY),
        _security_text(IDN_003_HEADLINE, IDN_003_PREHEADER, rows, text_body,
                       IDN_003_CTA, url, IDN_003_RECOVERY),
        sender=_sender_identity(SENDER_SECURITY),
    )


# --- IDN-004 Suspicious sign-in blocked ------------------------------------------------

IDN_004_SUBJECT = "Zoiko Steam blocked a suspicious sign-in"
IDN_004_PREHEADER = "No access was granted. Review the attempted sign-in."
IDN_004_HEADLINE = "We blocked an attempt to access your account."
IDN_004_CTA = "Secure your account"
IDN_004_RECOVERY = (
    "No access was granted and no changes were made to your account. If you do not "
    "recognize this attempt, change your password and review your account security."
)


def send_suspicious_sign_in_email(to, *, attempted_at, device, location, session_reference):
    """IDN-004.

    The body states WHAT happened and never WHY it was detected: no rule, threshold, counter
    or window appears anywhere in the message. The recovery note claims only what is true --
    no access granted, no change made -- because this platform performs no automatic session
    or credential revocation on a block.
    """
    body = (
        "Zoiko Steam blocked a sign-in attempt at "
        + html.escape(attempted_at)
        + " because it did not meet the required security checks. Review the approximate "
        "location, device, and recovery options."
    )
    text_body = (
        f"Zoiko Steam blocked a sign-in attempt at {attempted_at} because it did not meet "
        "the required security checks. Review the approximate location, device, and "
        "recovery options."
    )
    rows = [
        ("Attempted at", attempted_at),
        ("Device", device),
        ("Approximate location", location),
        ("Security reference", session_reference),
    ]
    url = security_url()
    return _send(
        to,
        IDN_004_SUBJECT,
        _security_shell("Suspicious sign-in blocked", IDN_004_HEADLINE, IDN_004_PREHEADER,
                        rows, body, IDN_004_CTA, url, IDN_004_RECOVERY),
        _security_text(IDN_004_HEADLINE, IDN_004_PREHEADER, rows, text_body,
                       IDN_004_CTA, url, IDN_004_RECOVERY),
        sender=_sender_identity(SENDER_SECURITY),
    )


# --- IDN-005 Password or passkey lifecycle ---------------------------------------------
# Only the base "password changed" variant is implemented. The approved "passkey added" and
# "passkey removed" variants are NOT built: no passkey subsystem exists in this codebase and
# inventing one to satisfy a template would be worse than reporting the gap.

IDN_005_SUBJECT = "Your Zoiko Steam sign-in method was changed"
IDN_005_PREHEADER = "Review this security change."
IDN_005_HEADLINE = "Your sign-in credentials were updated."
IDN_005_CTA = "Review account security"
IDN_005_RECOVERY = (
    "If you did not make this change, reset your password immediately and review your "
    "account security."
)

# The literal truth about this platform's sessions, and the only value passed today.
# Access tokens are stateless JWTs with no jti, no token version and no server-side session
# store (security.py), so a credential change cannot invalidate them: they remain valid
# until they expire. Claiming revocation here would be a false security assurance.
SESSION_EFFECT_NOT_REVOKED = "not revoked and remain active until they expire"


def send_credential_changed_email(to, *, credential_type, changed_at, session_effect,
                                   security_reference):
    """IDN-005. Never receives the password, its hash, the OTP or any token."""
    body = (
        "The "
        + html.escape(credential_type)
        + " associated with your Zoiko Steam identity was changed on "
        + html.escape(changed_at)
        + ". Existing sessions were "
        + html.escape(session_effect)
        + "."
    )
    text_body = (
        f"The {credential_type} associated with your Zoiko Steam identity was changed on "
        f"{changed_at}. Existing sessions were {session_effect}."
    )
    rows = [
        ("Credential", credential_type),
        ("Changed at", changed_at),
        ("Security reference", security_reference),
    ]
    url = security_url()
    return _send(
        to,
        IDN_005_SUBJECT,
        _security_shell("Sign-in method changed", IDN_005_HEADLINE, IDN_005_PREHEADER, rows,
                        body, IDN_005_CTA, url, IDN_005_RECOVERY),
        _security_text(IDN_005_HEADLINE, IDN_005_PREHEADER, rows, text_body,
                       IDN_005_CTA, url, IDN_005_RECOVERY),
        sender=_sender_identity(SENDER_SECURITY),
    )


# --- IDN-006 / IDN-007 / IDN-008 - Security, recovery and account lifecycle -------------
# All Class A (IDN-008 is Security/legal). Sender Zoiko Steam Security, recipient the
# account holder (IDN-007 additionally copies an APPROVED recovery contact). Mandatory:
# nothing in this module consults a preference, and three tests assert that.
#
# Shared controls: HTML + plain text, no tracking pixel, no remote asset, no marketing,
# UTC-labelled timestamps, no secret of any kind, and no internal detection detail.


def recovery_contact_url(token):
    """IDN-006 confirm link for a nominated recovery address.

    Carries an opaque single-use token and nothing else -- no address, no user id. The
    address being confirmed is already known to whoever opens their own inbox.
    """
    return public_base_url() + "/confirm-recovery-contact?token=" + quote(token, safe="")


# --- IDN-006 MFA and recovery settings --------------------------------------------------
# ONLY the "recovery method changed" variant is implemented.
#
# The two MFA variants are deliberately NOT built. `organizations.security.require_2fa` is
# stored and reported (services/org.py) but nothing enforces it: there is no MFA challenge,
# no enrolment and no verification step anywhere in this codebase. Emitting "MFA is now
# enabled for your Zoiko Steam account" when flipping that flag changes no security property
# would be a false security assurance -- the single worst thing a Class A message can do.
# Reported as a missing subsystem instead.

IDN_006_SUBJECT = "Zoiko Steam security settings changed"
IDN_006_PREHEADER = "MFA or recovery information was updated."
IDN_006_HEADLINE = "Review your account-security change."
IDN_006_CTA = "Review security settings"
IDN_006_RECOVERY_NOTE = (
    "If you did not authorize this change, begin account recovery immediately."
)

# Canonical variant copy. change_description is rendered into the body sentence.
IDN_006_RECOVERY_METHOD_CHANGED = "Your recovery method"


def send_security_setting_changed_email(to, *, change_description, changed_at,
                                        masked_destination=None):
    """IDN-006. `masked_destination` is already masked by crud.recovery.mask_destination --
    the full recovery address is never passed in and never rendered."""
    body = (
        html.escape(change_description)
        + " was completed at "
        + html.escape(changed_at)
        + ". "
        + IDN_006_RECOVERY_NOTE
    )
    text_body = (
        change_description + " was completed at " + changed_at + ". " + IDN_006_RECOVERY_NOTE
    )
    rows = [("Change", change_description), ("Completed at", changed_at)]
    if masked_destination:
        rows.append(("Recovery destination", masked_destination))
    url = security_url()
    return _send(
        to,
        IDN_006_SUBJECT,
        _security_shell("Security settings changed", IDN_006_HEADLINE, IDN_006_PREHEADER,
                        rows, body, IDN_006_CTA, url, IDN_006_RECOVERY_NOTE),
        _security_text(IDN_006_HEADLINE, IDN_006_PREHEADER, rows, text_body,
                       IDN_006_CTA, url, IDN_006_RECOVERY_NOTE),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_recovery_contact_verification_email(to, *, confirm_url, expires_at):
    """Sent TO the nominated address to prove control before it is honoured.

    Not itself an approved IDN template -- it is the verification step that makes the
    IDN-006 "recovery method changed" notice truthful. Without it, "set recovery email"
    would be an account-takeover primitive.
    """
    headline = "Confirm this recovery address."
    body = ("This address was nominated as a recovery destination for a Zoiko Steam "
            "account. It will only be used for recovery once you confirm it below.")
    rows = [("Expires at", expires_at)]
    note = "If you did not expect this, you can safely ignore this email."
    return _send(
        to,
        "Confirm your Zoiko Steam recovery address",
        _security_shell("Confirm recovery address", headline,
                        "Confirm this address before it is used for recovery.", rows,
                        html.escape(body), "Confirm recovery address", confirm_url, note),
        _security_text(headline, "Confirm this address before it is used for recovery.",
                       rows, body, "Confirm recovery address", confirm_url, note),
        sender=_sender_identity(SENDER_SECURITY),
    )


# --- IDN-007 Account recovery lifecycle -------------------------------------------------

IDN_007_SUBJECT_STARTED = "Zoiko Steam account recovery started"
IDN_007_SUBJECT_VERIFICATION = "Action required to recover your Zoiko Steam account"
IDN_007_SUBJECT_COMPLETED = "Your Zoiko Steam account recovery is complete"
IDN_007_SUBJECT_CANCELED = "Your Zoiko Steam account recovery request was canceled"

IDN_007_PREHEADER = "Review the request and required security steps."
IDN_007_HEADLINE = "An account-recovery request is in progress."
IDN_007_CTA = "Review recovery request"

# Mandated verbatim wherever a one-time code appears. This is the anti-social-engineering
# line: the single most effective countermeasure against a caller talking a code out of
# someone, and its absence was a recorded audit finding.
CODE_NEVER_READ_ALOUD = "Zoiko Steam Support will never ask you to read this code aloud."


def send_recovery_started_email(to, *, started_at, security_reference):
    body = (
        "A recovery request for your Zoiko Steam identity began at "
        + html.escape(started_at)
        + ". No access changes are complete until all required verification and risk "
        "checks pass."
    )
    text_body = (
        f"A recovery request for your Zoiko Steam identity began at {started_at}. No access "
        "changes are complete until all required verification and risk checks pass."
    )
    note = ("If you did not start this, no action is required -- the request cannot complete "
            "without the verification step. Review your account security to be sure.")
    rows = [("Started at", started_at), ("Security reference", security_reference)]
    url = security_url()
    return _send(
        to,
        IDN_007_SUBJECT_STARTED,
        _security_shell("Account recovery started", IDN_007_HEADLINE, IDN_007_PREHEADER,
                        rows, body, IDN_007_CTA, url, note),
        _security_text(IDN_007_HEADLINE, IDN_007_PREHEADER, rows, text_body,
                       IDN_007_CTA, url, note),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_recovery_verification_email(to, *, code, expires_at):
    """The 'Additional verification' variant. Carries the one-time code itself.

    The code is the ONLY sensitive value any of these templates renders, and it is a
    short-lived single-use value the recipient must have. Identity documents are never
    requested by email -- the baseline forbids it and nothing here asks for them.
    """
    headline = "Action required to recover your account."
    body = ("Use the verification code below to continue recovering your Zoiko Steam "
            "account. We will never ask for identity documents by email.")
    rows = [("Verification code", code), ("Expires at", expires_at)]
    url = security_url()
    return _send(
        to,
        IDN_007_SUBJECT_VERIFICATION,
        _security_shell("Additional verification required", headline, IDN_007_PREHEADER,
                        rows, html.escape(body), IDN_007_CTA, url, CODE_NEVER_READ_ALOUD),
        _security_text(headline, IDN_007_PREHEADER, rows, body, IDN_007_CTA, url,
                       CODE_NEVER_READ_ALOUD),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_recovery_completed_email(to, *, completed_at, credentials_reset, session_effect,
                                   security_reference):
    headline = "Your account recovery is complete."
    body = (
        "Recovery of your Zoiko Steam identity completed at "
        + html.escape(completed_at)
        + ". Review your account security now."
    )
    text_body = (
        f"Recovery of your Zoiko Steam identity completed at {completed_at}. Review your "
        "account security now."
    )
    note = ("If you did not perform this recovery, contact support immediately -- someone "
            "else may control your account.")
    rows = [
        ("Completed at", completed_at),
        ("Credentials reset", credentials_reset),
        ("Existing sessions", session_effect),
        ("Security reference", security_reference),
    ]
    url = security_url()
    return _send(
        to,
        IDN_007_SUBJECT_COMPLETED,
        _security_shell("Account recovery complete", headline, IDN_007_PREHEADER, rows,
                        body, IDN_007_CTA, url, note),
        _security_text(headline, IDN_007_PREHEADER, rows, text_body, IDN_007_CTA, url, note),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_recovery_canceled_email(to, *, canceled_at, account_state, security_reference):
    headline = "Your account recovery request was canceled."
    body = (
        "The recovery request for your Zoiko Steam identity was canceled at "
        + html.escape(canceled_at)
        + ". No credentials or sessions were changed."
    )
    text_body = (
        f"The recovery request for your Zoiko Steam identity was canceled at {canceled_at}. "
        "No credentials or sessions were changed."
    )
    note = "If you did not expect this, review your account security."
    rows = [
        ("Canceled at", canceled_at),
        ("Account state", account_state),
        ("Security reference", security_reference),
    ]
    url = security_url()
    return _send(
        to,
        IDN_007_SUBJECT_CANCELED,
        _security_shell("Account recovery canceled", headline, IDN_007_PREHEADER, rows,
                        body, IDN_007_CTA, url, note),
        _security_text(headline, IDN_007_PREHEADER, rows, text_body, IDN_007_CTA, url, note),
        sender=_sender_identity(SENDER_SECURITY),
    )


# --- IDN-008 Account restriction and deletion lifecycle ---------------------------------
# Implemented: the base "restricted" notice, its reactivation counterpart, and
# "deletion completed".
#
# NOT implemented: "Deletion scheduled" and "Deletion canceled". Both require a scheduling
# lifecycle -- an effective date and a cancellation deadline the platform will actually
# honour -- and no such subsystem exists. A scheduled-deletion notice would promise a
# cancellation window nothing can enforce, which is exactly the kind of false statement the
# residual-records rule exists to prevent. Reported as a product gap.

IDN_008_SUBJECT_RESTRICTED = "Your Zoiko Steam account is restricted"
IDN_008_SUBJECT_REACTIVATED = "Your Zoiko Steam account access was restored"
IDN_008_SUBJECT_DELETED = "Your Zoiko Steam account deletion was completed"

IDN_008_PREHEADER = "Review the reason category and available next steps."
IDN_008_HEADLINE = "Your access has changed."
IDN_008_CTA = "Review account status"

# Deliberate wording. Access ends; records do not necessarily. Saying "all your data has
# been permanently erased" would be false -- audit records survive by design and by law.
RESIDUAL_RECORDS_NOTE = (
    "Active access has been removed. Some records are retained where required for security, "
    "audit, legal, fraud-prevention or contractual retention obligations, so this does not "
    "mean every record has been erased."
)


def send_account_restricted_email(to, *, account_state, effective_at, org_effect,
                                   security_reference):
    """IDN-008 base + reactivation. Reason CATEGORY only -- never detection detail."""
    reactivated = account_state.lower().startswith("active")
    subject = IDN_008_SUBJECT_REACTIVATED if reactivated else IDN_008_SUBJECT_RESTRICTED
    headline = "Your access has been restored." if reactivated else IDN_008_HEADLINE
    body = (
        "Your Zoiko Steam identity is currently "
        + html.escape(account_state)
        + " effective "
        + html.escape(effective_at)
        + ". Review the secure account record for scope, reason category, appeal or "
        "recovery route, and any effect on Organization access."
    )
    text_body = (
        f"Your Zoiko Steam identity is currently {account_state} effective {effective_at}. "
        "Review the secure account record for scope, reason category, appeal or recovery "
        "route, and any effect on Organization access."
    )
    note = ("To ask about this decision, contact Zoiko Steam Support with the security "
            "reference above.")
    rows = [
        ("Account state", account_state),
        ("Effective", effective_at),
        ("Organization access", org_effect),
        ("Security reference", security_reference),
    ]
    url = security_url()
    return _send(
        to,
        subject,
        _security_shell("Account status changed", headline, IDN_008_PREHEADER, rows, body,
                        IDN_008_CTA, url, note),
        _security_text(headline, IDN_008_PREHEADER, rows, text_body, IDN_008_CTA, url, note),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_account_deleted_email(to, *, effective_at, org_effect, security_reference):
    """IDN-008 'Deletion completed'."""
    headline = "Your account deletion was completed."
    body = (
        "Your Zoiko Steam account was deleted effective "
        + html.escape(effective_at)
        + ". "
        + RESIDUAL_RECORDS_NOTE
    )
    text_body = (
        f"Your Zoiko Steam account was deleted effective {effective_at}. "
        + RESIDUAL_RECORDS_NOTE
    )
    note = ("To ask about this deletion or about retained records, contact Zoiko Steam "
            "Support with the security reference above.")
    rows = [
        ("Deleted effective", effective_at),
        ("Organization access", org_effect),
        ("Security reference", security_reference),
    ]
    url = security_url()
    return _send(
        to,
        IDN_008_SUBJECT_DELETED,
        _security_shell("Account deleted", headline, IDN_008_PREHEADER, rows, body,
                        IDN_008_CTA, url, note),
        _security_text(headline, IDN_008_PREHEADER, rows, text_body, IDN_008_CTA, url, note),
        sender=_sender_identity(SENDER_SECURITY),
    )


def _account_ready_html(app_url: str) -> str:
    return _shell(f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;">{html.escape(IDN_002_PREHEADER)}</div>
    {_header("Your access is ready")}
    <div style="padding:24px 32px 40px;color:#333;font-size:16px;line-height:1.6;">
      <h2 style="margin:0 0 16px;font-size:18px;font-weight:600;color:#2e2e4d;">{IDN_002_HEADLINE}</h2>
      <p style="margin:0 0 24px;">{IDN_002_BODY}</p>
      <p style="text-align:center;margin:32px 0;">
        <a href="{html.escape(app_url, quote=True)}"
           style="background:#2e2e4d;color:#fff;text-decoration:none;padding:16px 32px;
           border-radius:4px;font-weight:bold;font-size:16px;display:inline-block;">
          {IDN_002_CTA}
        </a>
      </p>
      <p style="margin:0;color:#4a4a5a;font-size:14px;">
        If the button does not work, open
        <a href="{html.escape(app_url, quote=True)}" style="color:#2e2e4d;">{html.escape(app_url)}</a>.
      </p>
    </div>""")


def _account_ready_text(app_url: str) -> str:
    return "\n".join([
        IDN_002_HEADLINE,
        "",
        IDN_002_BODY,
        "",
        IDN_002_PREHEADER,
        "",
        f"{IDN_002_CTA}: {app_url}",
        "",
        "Zoiko Steam",
    ])


def send_account_ready_email(to: str, app_url: str) -> bool:
    """IDN-002. Returns True only if Resend accepted the message.

    No recipient name is interpolated and no account detail is included: the message says
    access exists and points at the sign-in page. Everything about what that access covers
    lives behind authentication, where it can be shown accurately.
    """
    return _send(
        to,
        IDN_002_SUBJECT,
        _account_ready_html(app_url),
        _account_ready_text(app_url),
        sender=_sender_identity(SENDER_DEFAULT),
    )


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

# Copy note: this is a product-approved variant of the ZST-EC-001 v2.0 base copy, adopted
# to make first contact welcoming while keeping verification the single job of the message.
# The baseline's own wording was "Confirm your email address." / "A request was made to
# verify this email address…". Per Section 04 that substitution is a governance decision,
# not an implementation detail — record it before this ships to production.
#
# What did NOT change, because it is what makes the message safe: one dominant action, no
# claim that the account is usable yet, and the explicit statement that sign-in comes after
# verification. IDN-002 remains the only message allowed to say access is active.
IDN_001_SUBJECT = "Verify your email for Zoiko Steam"
IDN_001_HEADLINE = "Welcome to Zoiko Steam"
IDN_001_BODY = (
    "Thanks for creating your Zoiko Stream account. Before you can sign in, please "
    "verify your email address using the secure button below."
)
IDN_001_CTA = "Verify email"
IDN_001_AFTER_CTA = (
    "Once your email is verified, you can sign in using the email address and password "
    "you created."
)
IDN_001_IGNORE = "If you did not create this account, you can safely ignore this email."


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
      <p style="margin:0 0 24px;">{IDN_001_AFTER_CTA}</p>
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
        IDN_001_AFTER_CTA,
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


# == ORG-001 Organization invitation lifecycle ===========================================
# Class C (Transactional) - Sender: Zoiko Steam - Recipient: invitee (+ inviter where the
# variant calls for it). Four approved variants: base, Reminder, Expired, Revoked.
#
# This REPLACES the former "You're invited" template, which said only "This link expires
# soon" while Invitation.expires_at held an exact deadline, carried no role, and shipped
# HTML with no plain-text alternative. Corrected in place rather than added alongside so
# the vague copy cannot still be reached.
#
# Scope honesty. Invitation.role is authoritative and is rendered. There is no Workspace
# entity and no mode-access concept in this codebase (services/org.py treats an
# organization AS its single production workspace), so the workspace line is rendered from
# that real implicit workspace and mode access is OMITTED rather than invented. See the
# reported domain gap.

ORG_001_SUBJECT = "You were invited to {org} on Zoiko Steam"
ORG_001_PREHEADER = "Review the role, workspace, and invitation expiry."
ORG_001_CTA = "Review invitation"

ORG_001_REMINDER_SUBJECT = "Reminder: your Zoiko Steam invitation expires soon"
ORG_001_EXPIRED_SUBJECT = "Your Zoiko Steam invitation expired"
ORG_001_REVOKED_SUBJECT = "Your Zoiko Steam invitation was revoked"

# Load-bearing: an invitation is an offer, not access. Every variant states this so the
# message can never read as though membership already exists.
ORG_001_NOT_YET_MEMBER = (
    "This invitation is an offer of access. It does not create membership, and it grants "
    "no access to this Organization until you accept it."
)
ORG_001_EXPIRED_ROUTE = (
    "To request a new invitation, contact an authorized administrator of this Organization."
)


def _org_shell(header_title, headline, preheader, rows, body, cta_label, cta_url, footer_note):
    """Shared Organization layout. Same bones as the Class A shell so the two families
    stay visually one product; kept separate because the senders and the suppression rules
    differ and collapsing them would invite a Class C change from leaking into Class A."""
    cta = ""
    if cta_label and cta_url:
        cta = (
            '<p style="text-align:center;margin:32px 0;">'
            '<a href="' + html.escape(cta_url, quote=True) + '" '
            'style="background:#2e2e4d;color:#fff;text-decoration:none;padding:16px 32px;'
            'border-radius:4px;font-weight:bold;font-size:16px;display:inline-block;">'
            + html.escape(cta_label) + "</a></p>"
        )
    return _shell(f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;">{html.escape(preheader)}</div>
    {_header(header_title)}
    <div style="padding:24px 32px 40px;color:#333;font-size:16px;line-height:1.6;">
      <h2 style="margin:0 0 16px;font-size:18px;font-weight:600;color:#2e2e4d;">{html.escape(headline)}</h2>
      <p style="margin:0 0 20px;">{body}</p>
      <table style="width:100%;border-collapse:collapse;margin:0 0 24px;">{_security_rows_html(rows)}</table>
      {cta}
      <p style="margin:0;color:#4a4a5a;font-size:14px;">{footer_note}</p>
    </div>""")


def _org_text(headline, preheader, rows, body, cta_label, cta_url, footer_note, signature):
    lines = [headline, "", body, "", preheader, ""]
    lines += [f"{k}: {v}" for k, v in rows]
    if cta_label and cta_url:
        lines += ["", f"{cta_label}: {cta_url}"]
    lines += ["", footer_note, "", signature]
    return chr(10).join(lines)


def invitation_url(token: str) -> str:
    """ORG-001 CTA. Carries the opaque invitation token and nothing else.

    The token is purpose-bound (it resolves only through find_invitation_by_token), expiry-
    bound (Invitation.expires_at) and revocable (status flips to cancelled/expired). No
    email address, org id, user id or role travels in the query string.
    """
    return f"{public_base_url()}/accept-invite?token={quote(token, safe='')}"


def organizations_url() -> str:
    """ORG-004 CTA. The signed-in landing page, which is where a member can see which
    Organizations they still belong to. No token, no identifier."""
    return f"{public_base_url()}/login"


def _invitation_rows(*, role_name, workspace_scope, expires_display, mode_access=None):
    rows = [("Role", role_name), ("Workspace scope", workspace_scope)]
    # Mode access is only rendered when the caller can supply an authoritative value.
    # This platform has none today, so it is normally absent rather than guessed.
    if mode_access:
        rows.append(("Mode access", mode_access))
    rows.append(("Expires", expires_display))
    return rows


def send_invitation_email(to, *, org_name, inviter_name, role_name, workspace_scope,
                          expires_display, invite_url, mode_access=None):
    """ORG-001 base variant."""
    headline = f"Join {org_name} on Zoiko Steam."
    body_txt = (
        f"{inviter_name} invited you to access {org_name} with role {role_name} across "
        f"{workspace_scope}. Review the mode access and expiration before accepting."
    )
    rows = _invitation_rows(role_name=role_name, workspace_scope=workspace_scope,
                            expires_display=expires_display, mode_access=mode_access)
    return _send(
        to,
        ORG_001_SUBJECT.format(org=org_name),
        _org_shell("You're invited", headline, ORG_001_PREHEADER, rows,
                   html.escape(body_txt), ORG_001_CTA, invite_url, ORG_001_NOT_YET_MEMBER),
        _org_text(headline, ORG_001_PREHEADER, rows, body_txt, ORG_001_CTA, invite_url,
                  ORG_001_NOT_YET_MEMBER, "Zoiko Steam"),
        sender=_sender_identity(SENDER_DEFAULT),
    )


def send_invitation_reminder_email(to, *, org_name, inviter_name, role_name, workspace_scope,
                                   expires_display, invite_url, mode_access=None):
    """ORG-001 Reminder. Exact expiry with its timezone; must not imply membership exists."""
    headline = f"Your invitation to {org_name} expires soon."
    body_txt = (
        f"{inviter_name} invited you to access {org_name} with role {role_name} across "
        f"{workspace_scope}. This invitation expires at {expires_display} and cannot be "
        f"accepted after that time."
    )
    rows = _invitation_rows(role_name=role_name, workspace_scope=workspace_scope,
                            expires_display=expires_display, mode_access=mode_access)
    return _send(
        to,
        ORG_001_REMINDER_SUBJECT,
        _org_shell("Invitation expiring", headline, ORG_001_PREHEADER, rows,
                   html.escape(body_txt), ORG_001_CTA, invite_url, ORG_001_NOT_YET_MEMBER),
        _org_text(headline, ORG_001_PREHEADER, rows, body_txt, ORG_001_CTA, invite_url,
                  ORG_001_NOT_YET_MEMBER, "Zoiko Steam"),
        sender=_sender_identity(SENDER_DEFAULT),
    )


def send_invitation_expired_email(to, *, org_name, role_name, expires_display):
    """ORG-001 Expired. No CTA: there is nothing valid left to accept, and offering a
    button that leads to a rejection would be worse than offering none."""
    headline = f"Your invitation to {org_name} has expired."
    body_txt = (
        f"The invitation to access {org_name} with role {role_name} expired at "
        f"{expires_display} and can no longer be accepted."
    )
    rows = [("Organization", org_name), ("Role", role_name), ("Expired", expires_display)]
    footer = f"{ORG_001_EXPIRED_ROUTE} {ORG_001_NOT_YET_MEMBER}"
    return _send(
        to,
        ORG_001_EXPIRED_SUBJECT,
        _org_shell("Invitation expired", headline, ORG_001_PREHEADER, rows,
                   html.escape(body_txt), None, None, footer),
        _org_text(headline, ORG_001_PREHEADER, rows, body_txt, None, None,
                  footer, "Zoiko Steam"),
        sender=_sender_identity(SENDER_DEFAULT),
    )


def send_invitation_revoked_email(to, *, org_name, role_name, effective_at):
    """ORG-001 Revoked. Effective time only - the reason is deliberately not disclosed."""
    headline = f"Your invitation to {org_name} was revoked."
    body_txt = (
        f"The invitation to access {org_name} with role {role_name} was revoked at "
        f"{effective_at} and can no longer be accepted."
    )
    rows = [("Organization", org_name), ("Role", role_name), ("Effective", effective_at)]
    footer = f"{ORG_001_EXPIRED_ROUTE} {ORG_001_NOT_YET_MEMBER}"
    return _send(
        to,
        ORG_001_REVOKED_SUBJECT,
        _org_shell("Invitation revoked", headline, ORG_001_PREHEADER, rows,
                   html.escape(body_txt), None, None, footer),
        _org_text(headline, ORG_001_PREHEADER, rows, body_txt, None, None,
                  footer, "Zoiko Steam"),
        sender=_sender_identity(SENDER_DEFAULT),
    )


# == ORG-007 Access review lifecycle =====================================================
# Class A (Security / governance) - Sender: Zoiko Steam Security - Recipients: assigned
# reviewers + Organization owner.
#
# The controls that matter here are in the domain, not the copy: a reviewer's silence leaves
# the decision PENDING, never APPROVED. The copy says so explicitly, because a reviewer who
# believes inaction equals approval will use inaction as approval.

ORG_007_SUBJECT = "Access review assigned for {org}"
ORG_007_REMINDER_SUBJECT = "Reminder: Zoiko Steam access review is due soon"
ORG_007_OVERDUE_SUBJECT = "Action required: Zoiko Steam access review is overdue"
ORG_007_COMPLETED_SUBJECT = "Access review completed for {org}"
ORG_007_PREHEADER = "Review member access by {due}."
ORG_007_HEADLINE = "Confirm that access remains necessary."
ORG_007_BODY = (
    "Review each listed member, role, workspace, and mode. Exceptions require a recorded "
    "reason and accountable owner."
)
ORG_007_CTA = "Start access review"
ORG_007_NO_IMPLICIT_APPROVAL = (
    "No response is not approval. Any member you do not decide on stays pending and the "
    "review cannot complete."
)


def access_review_url() -> str:
    """Authenticated Tenant Console destination. No token, no identifiers."""
    return f"{public_base_url()}/organization/settings?tab=security"


def send_access_review_assigned_email(to, *, org_name, due_display, outstanding,
                                      review_period=None):
    rows = [("Organization", org_name), ("Due", due_display),
            ("Members to review", str(outstanding))]
    if review_period:
        rows.insert(1, ("Review period", review_period))
    url = access_review_url()
    pre = ORG_007_PREHEADER.format(due=due_display)
    return _send(
        to, ORG_007_SUBJECT.format(org=org_name),
        _org_shell("Access review", ORG_007_HEADLINE, pre, rows,
                   html.escape(ORG_007_BODY), ORG_007_CTA, url, ORG_007_NO_IMPLICIT_APPROVAL),
        _org_text(ORG_007_HEADLINE, pre, rows, ORG_007_BODY, ORG_007_CTA, url,
                  ORG_007_NO_IMPLICIT_APPROVAL, "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_access_review_reminder_email(to, *, org_name, due_display, outstanding):
    headline = "An access review is still open."
    body = (
        f"{outstanding} member(s) in {org_name} are still awaiting a decision. The review "
        f"is due at {due_display}."
    )
    rows = [("Organization", org_name), ("Due", due_display), ("Still pending", str(outstanding))]
    url = access_review_url()
    pre = ORG_007_PREHEADER.format(due=due_display)
    return _send(
        to, ORG_007_REMINDER_SUBJECT,
        _org_shell("Review due soon", headline, pre, rows, html.escape(body),
                   ORG_007_CTA, url, ORG_007_NO_IMPLICIT_APPROVAL),
        _org_text(headline, pre, rows, body, ORG_007_CTA, url,
                  ORG_007_NO_IMPLICIT_APPROVAL, "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_access_review_overdue_email(to, *, org_name, due_display, outstanding,
                                     escalation_note):
    headline = "An access review is overdue."
    body = (
        f"The access review for {org_name} passed its due date at {due_display} with "
        f"{outstanding} member(s) still pending."
    )
    rows = [("Organization", org_name), ("Was due", due_display),
            ("Still pending", str(outstanding)), ("Policy state", "Overdue")]
    url = access_review_url()
    footer = f"{escalation_note} {ORG_007_NO_IMPLICIT_APPROVAL}"
    return _send(
        to, ORG_007_OVERDUE_SUBJECT,
        _org_shell("Review overdue", headline, ORG_007_PREHEADER.format(due=due_display),
                   rows, html.escape(body), ORG_007_CTA, url, footer),
        _org_text(headline, ORG_007_PREHEADER.format(due=due_display), rows, body,
                  ORG_007_CTA, url, footer, "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_access_review_completed_email(to, *, org_name, completed_display, approved,
                                       changed, removed, exceptions, high_risk_exceptions):
    headline = "The access review is complete."
    body = (
        f"The access review for {org_name} completed at {completed_display}. Every member "
        f"in scope now carries a recorded decision."
    )
    rows = [("Organization", org_name), ("Completed", completed_display),
            ("Approved", str(approved)), ("Change required", str(changed)),
            ("Removed", str(removed)), ("Exceptions", str(exceptions))]
    if high_risk_exceptions:
        rows.append(("High-risk exceptions", str(high_risk_exceptions)))
    url = access_review_url()
    footer = ("Recorded exceptions remain the responsibility of their named owner until "
              "revisited.")
    return _send(
        to, ORG_007_COMPLETED_SUBJECT.format(org=org_name),
        _org_shell("Review completed", headline, "The access review is closed.", rows,
                   html.escape(body), "Review current access", url, footer),
        _org_text(headline, "The access review is closed.", rows, body,
                  "Review current access", url, footer, "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


# == ORG-008 Organization ownership transfer =============================================
# Class A (Security) - Sender: Zoiko Steam Security - Recipients: current owner, proposed
# owner, security administrators.
#
# The CTA lands on the authenticated console. It is NOT a confirmation link: clicking a link
# from an inbox is not authentication, and ownership of a tenant is not something an inbox
# should be able to move.

ORG_008_SUBJECT = "Confirm the transfer of Zoiko Steam Organization ownership"
ORG_008_COMPLETED_SUBJECT = "Zoiko Steam Organization ownership transferred"
ORG_008_EXPIRED_SUBJECT = "Zoiko Steam ownership-transfer request expired"
ORG_008_CANCELED_SUBJECT = "Zoiko Steam ownership-transfer request canceled"
ORG_008_PREHEADER = "An ownership transfer is awaiting confirmation."
ORG_008_HEADLINE = "Review the proposed ownership transfer."
ORG_008_CTA = "Review transfer"
ORG_008_NO_LINK_AUTH = (
    "Confirm this transfer by signing in to Zoiko Steam. Confirmation is never completed "
    "from an email link."
)
ORG_008_OWNERSHIP_UNCHANGED = "Ownership did not change."


def ownership_transfer_url() -> str:
    return f"{public_base_url()}/organization/settings?tab=security"


def send_ownership_transfer_email(to, *, org_name, current_owner, proposed_owner,
                                  expires_display, step_up_note):
    body = (
        f"Ownership of {org_name} is proposed to move from {current_owner} to "
        f"{proposed_owner}. The transfer will not complete until required confirmations and "
        f"security checks pass."
    )
    rows = [("Organization", org_name), ("Current owner", current_owner),
            ("Proposed owner", proposed_owner), ("Request expires", expires_display),
            ("Confirmations required", "Both current and proposed owner")]
    url = ownership_transfer_url()
    footer = f"{ORG_008_NO_LINK_AUTH} {step_up_note}"
    return _send(
        to, ORG_008_SUBJECT,
        _org_shell("Ownership transfer", ORG_008_HEADLINE, ORG_008_PREHEADER, rows,
                   html.escape(body), ORG_008_CTA, url, footer),
        _org_text(ORG_008_HEADLINE, ORG_008_PREHEADER, rows, body, ORG_008_CTA, url,
                  footer, "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_ownership_transferred_email(to, *, org_name, previous_owner, new_owner,
                                     effective_display, resulting_permissions):
    headline = "Organization ownership was transferred."
    body = (
        f"Ownership of {org_name} moved from {previous_owner} to {new_owner}, effective "
        f"{effective_display}."
    )
    rows = [("Organization", org_name), ("Previous owner", previous_owner),
            ("New owner", new_owner), ("Effective", effective_display),
            ("Resulting permissions", resulting_permissions)]
    url = ownership_transfer_url()
    return _send(
        to, ORG_008_COMPLETED_SUBJECT,
        _org_shell("Ownership transferred", headline, "Ownership has moved.", rows,
                   html.escape(body), "Review Organization settings", url,
                   "If you did not expect this, contact Zoiko Steam Support immediately."),
        _org_text(headline, "Ownership has moved.", rows, body,
                  "Review Organization settings", url,
                  "If you did not expect this, contact Zoiko Steam Support immediately.",
                  "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_ownership_transfer_expired_email(to, *, org_name, expired_display, current_owner):
    headline = "The ownership-transfer request expired."
    body = (
        f"The request to transfer ownership of {org_name} expired at {expired_display}. "
        f"{ORG_008_OWNERSHIP_UNCHANGED}"
    )
    rows = [("Organization", org_name), ("Expired", expired_display),
            ("Owner", current_owner), ("Outcome", ORG_008_OWNERSHIP_UNCHANGED)]
    url = ownership_transfer_url()
    return _send(
        to, ORG_008_EXPIRED_SUBJECT,
        _org_shell("Transfer expired", headline, ORG_008_OWNERSHIP_UNCHANGED, rows,
                   html.escape(body), "Review Organization settings", url,
                   "Start a new transfer request if ownership still needs to move."),
        _org_text(headline, ORG_008_OWNERSHIP_UNCHANGED, rows, body,
                  "Review Organization settings", url,
                  "Start a new transfer request if ownership still needs to move.",
                  "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_ownership_transfer_canceled_email(to, *, org_name, canceled_display, canceled_by,
                                           current_owner):
    headline = "The ownership-transfer request was canceled."
    body = (
        f"The request to transfer ownership of {org_name} was canceled by {canceled_by} at "
        f"{canceled_display}. {ORG_008_OWNERSHIP_UNCHANGED}"
    )
    rows = [("Organization", org_name), ("Canceled", canceled_display),
            ("Canceled by", canceled_by), ("Owner", current_owner),
            ("Outcome", ORG_008_OWNERSHIP_UNCHANGED)]
    url = ownership_transfer_url()
    return _send(
        to, ORG_008_CANCELED_SUBJECT,
        _org_shell("Transfer canceled", headline, ORG_008_OWNERSHIP_UNCHANGED, rows,
                   html.escape(body), "Review Organization settings", url,
                   "No permissions changed as a result of this request."),
        _org_text(headline, ORG_008_OWNERSHIP_UNCHANGED, rows, body,
                  "Review Organization settings", url,
                  "No permissions changed as a result of this request.",
                  "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


# == ORG-009 Authorized support access lifecycle =========================================
# Class A (Security) - Sender: Zoiko Steam Security - Recipients: Organization approver +
# affected administrators.
#
# Every CTA in this family lands on the TENANT console. Nothing here may link a customer to
# a Super Admin surface, and nothing here carries session material, credentials or internal
# investigation detail. The engineer is named by an approved professional identity so the
# customer can tell who was authorized without being handed an internal account record.

ORG_009_SUBJECT = "Approval requested for Zoiko Steam Support access"
ORG_009_STARTED_SUBJECT = "Zoiko Steam Support session started"
ORG_009_EXPIRING_SUBJECT = "Zoiko Steam Support session expires soon"
ORG_009_ENDED_SUBJECT = "Zoiko Steam Support session ended"
ORG_009_EMERGENCY_SUBJECT = "Important: emergency support access was used"
ORG_009_PREHEADER = "Review a time-limited, scoped support-session request."
ORG_009_HEADLINE = "Support access requires your approval."
ORG_009_CTA = "Review support request"
ORG_009_AUDIT_TERMS = (
    "Every action taken during an approved session is attributed to the named engineer and "
    "recorded in your Organization's audit history."
)
ORG_009_NO_OPEN_ENDED = (
    "Support access is always scoped and time-limited. Extending it requires a new request "
    "and a new approval."
)


def support_access_url() -> str:
    """Tenant console support-access surface. Never a Super Admin URL."""
    return f"{public_base_url()}/organization/support"


def _support_rows(*, case_reference, engineer, scope, allowed_actions, duration):
    return [("Support case", case_reference), ("Engineer", engineer),
            ("Permitted resources", scope), ("Allowed actions", allowed_actions),
            ("Requested duration", duration)]


def send_support_access_requested_email(to, *, org_name, case_reference, engineer, scope,
                                        allowed_actions, duration, reason_category):
    body = (
        f"Zoiko Steam Support requested scoped access for case {case_reference}. Review "
        f"permitted resources, allowed actions, duration, assigned engineer, and audit terms."
    )
    rows = [("Organization", org_name)] + _support_rows(
        case_reference=case_reference, engineer=engineer, scope=scope,
        allowed_actions=allowed_actions, duration=duration)
    rows.append(("Reason", reason_category))
    url = support_access_url()
    footer = f"{ORG_009_AUDIT_TERMS} {ORG_009_NO_OPEN_ENDED}"
    return _send(
        to, ORG_009_SUBJECT,
        _org_shell("Support access", ORG_009_HEADLINE, ORG_009_PREHEADER, rows,
                   html.escape(body), ORG_009_CTA, url, footer),
        _org_text(ORG_009_HEADLINE, ORG_009_PREHEADER, rows, body, ORG_009_CTA, url,
                  footer, "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_support_access_started_email(to, *, org_name, case_reference, engineer, scope,
                                      allowed_actions, started_display, expires_display,
                                      approved_by):
    headline = "An approved support session started."
    body = (
        f"The support session for case {case_reference} started at {started_display} and "
        f"ends automatically at {expires_display}."
    )
    rows = [("Organization", org_name), ("Support case", case_reference),
            ("Engineer", engineer), ("Approved scope", scope),
            ("Allowed actions", allowed_actions), ("Started", started_display),
            ("Ends automatically", expires_display), ("Approved by", approved_by)]
    url = support_access_url()
    return _send(
        to, ORG_009_STARTED_SUBJECT,
        _org_shell("Support session started", headline, "A scoped support session is active.",
                   rows, html.escape(body), "Review support access", url,
                   ORG_009_AUDIT_TERMS),
        _org_text(headline, "A scoped support session is active.", rows, body,
                  "Review support access", url, ORG_009_AUDIT_TERMS, "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_support_access_expiring_email(to, *, org_name, case_reference, engineer,
                                       expires_display, minutes_left):
    headline = "The support session is about to end."
    body = (
        f"The support session for case {case_reference} ends at {expires_display}, in about "
        f"{minutes_left} minutes."
    )
    rows = [("Organization", org_name), ("Support case", case_reference),
            ("Engineer", engineer), ("Ends", expires_display)]
    url = support_access_url()
    return _send(
        to, ORG_009_EXPIRING_SUBJECT,
        _org_shell("Support session expiring", headline, "No action is required to end it.",
                   rows, html.escape(body), "Review support access", url,
                   ORG_009_NO_OPEN_ENDED),
        _org_text(headline, "No action is required to end it.", rows, body,
                  "Review support access", url, ORG_009_NO_OPEN_ENDED, "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_support_access_ended_email(to, *, org_name, case_reference, engineer,
                                    ended_display, outcome, action_count):
    headline = "The support session ended."
    body = (
        f"The support session for case {case_reference} ended at {ended_display}. The "
        f"engineer no longer holds approved access to {org_name}."
    )
    rows = [("Organization", org_name), ("Support case", case_reference),
            ("Engineer", engineer), ("Ended", ended_display), ("Outcome", outcome),
            ("Recorded actions", str(action_count))]
    url = support_access_url()
    return _send(
        to, ORG_009_ENDED_SUBJECT,
        _org_shell("Support session ended", headline, "Access has been withdrawn.", rows,
                   html.escape(body), "View action history", url, ORG_009_AUDIT_TERMS),
        _org_text(headline, "Access has been withdrawn.", rows, body,
                  "View action history", url, ORG_009_AUDIT_TERMS, "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_support_access_emergency_email(to, *, org_name, case_reference, engineer,
                                        started_display, expires_display, emergency_reason,
                                        dual_authorization, review_note):
    headline = "Emergency support access was used."
    body = (
        f"Emergency support access to {org_name} was used for case {case_reference} starting "
        f"{started_display}. This access began without prior approval because it was "
        f"declared an emergency, and it is subject to review."
    )
    rows = [("Organization", org_name), ("Support case", case_reference),
            ("Engineer", engineer), ("Started", started_display),
            ("Ends automatically", expires_display),
            ("Declared reason", emergency_reason),
            ("Dual authorization", dual_authorization)]
    url = support_access_url()
    footer = f"{review_note} {ORG_009_AUDIT_TERMS}"
    return _send(
        to, ORG_009_EMERGENCY_SUBJECT,
        _org_shell("Emergency access", headline, "Review this access now.", rows,
                   html.escape(body), "Review support access", url, footer),
        _org_text(headline, "Review this access now.", rows, body,
                  "Review support access", url, footer, "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


# == ORG-010 Organization operational restriction ========================================
# Class A (Security / legal) - Sender: Zoiko Steam Security - Recipients: Organization owner
# + security administrators.
#
# The reason is a coarse approved category only. Fraud rules, detection logic, risk scores,
# employee notes, investigation detail and any mention of another customer are all absent by
# construction: the send function accepts a category and a recovery line, and nothing else.

ORG_010_SUBJECT = "Your Zoiko Steam Organization is {state}"
ORG_010_REACTIVATED_SUBJECT = "Your Zoiko Steam Organization is active again"
ORG_010_PREHEADER = "Review the scope, effective time, and required action."
ORG_010_HEADLINE = "Organization access has changed."
ORG_010_CTA = "Review Organization status"

ORG_010_REASON_LABELS = {
    "billing_commercial_requirement": "Billing / commercial requirement",
    "security_requirement": "Security requirement",
    "policy_compliance_requirement": "Policy / compliance requirement",
    "administrative_restriction": "Administrative restriction",
}


def organization_status_url() -> str:
    return f"{public_base_url()}/organization/support"


def send_organization_restricted_email(to, *, org_name, state_label, effective_display,
                                        reason_label, affected_capabilities, recovery_criteria,
                                        preserved_access):
    body = (
        f"{org_name} entered the {state_label} state at {effective_display}. Review affected "
        f"capabilities, reason category, customer obligations, recovery criteria, and support "
        f"route."
    )
    rows = [("Organization", org_name), ("State", state_label),
            ("Effective", effective_display), ("Reason", reason_label),
            ("Affected capabilities", affected_capabilities),
            ("Preserved access", preserved_access),
            ("To restore", recovery_criteria)]
    url = organization_status_url()
    return _send(
        to, ORG_010_SUBJECT.format(state=state_label.lower()),
        _org_shell("Organization status", ORG_010_HEADLINE, ORG_010_PREHEADER, rows,
                   html.escape(body), ORG_010_CTA, url,
                   "Contact Zoiko Steam Support through your Organization's support route "
                   "to resolve this."),
        _org_text(ORG_010_HEADLINE, ORG_010_PREHEADER, rows, body, ORG_010_CTA, url,
                  "Contact Zoiko Steam Support through your Organization's support route "
                  "to resolve this.", "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


def send_organization_reactivated_email(to, *, org_name, effective_display,
                                         restored_capabilities, remaining_restrictions):
    headline = "Organization access has been restored."
    body = (
        f"{org_name} returned to the active state at {effective_display}. Review what has "
        f"been restored and anything that remains limited."
    )
    rows = [("Organization", org_name), ("State", "Active"),
            ("Effective", effective_display),
            ("Restored capabilities", restored_capabilities),
            ("Remaining restrictions", remaining_restrictions)]
    url = organization_status_url()
    return _send(
        to, ORG_010_REACTIVATED_SUBJECT,
        _org_shell("Organization active", headline, "Access has been restored.", rows,
                   html.escape(body), ORG_010_CTA, url,
                   "If anything still appears limited, contact Zoiko Steam Support."),
        _org_text(headline, "Access has been restored.", rows, body, ORG_010_CTA, url,
                  "If anything still appears limited, contact Zoiko Steam Support.",
                  "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


# == ORG-002 Member joined ===============================================================
# Class C (Transactional) - Sender: Zoiko Steam - Recipients: inviter + org administrators.
# Trigger: invitation accepted AND membership committed.

ORG_002_SUBJECT = "{member} joined {org} on Zoiko Steam"
ORG_002_PREHEADER = "The Organization invitation was accepted."
ORG_002_HEADLINE = "A new member joined your Organization."
ORG_002_CTA = "View members"
ORG_002_REASON = (
    "You received this because you administer this Organization or issued this invitation."
)


def members_url() -> str:
    return f"{public_base_url()}/organization/members"


def send_member_joined_email(to, *, member_name, org_name, inviter_name, accepted_at,
                             role_summary, workspace_scope):
    """ORG-002. Sent to the inviter and to eligible organization administrators."""
    body_txt = (
        f"{member_name} accepted the invitation issued by {inviter_name} at {accepted_at} "
        f"with roles {role_summary} and workspace scope {workspace_scope}."
    )
    rows = [("Member", member_name), ("Organization", org_name), ("Invited by", inviter_name),
            ("Accepted", accepted_at), ("Roles", role_summary),
            ("Workspace scope", workspace_scope)]
    url = members_url()
    return _send(
        to,
        ORG_002_SUBJECT.format(member=member_name, org=org_name),
        _org_shell("New member", ORG_002_HEADLINE, ORG_002_PREHEADER, rows,
                   html.escape(body_txt), ORG_002_CTA, url, ORG_002_REASON),
        _org_text(ORG_002_HEADLINE, ORG_002_PREHEADER, rows, body_txt, ORG_002_CTA, url,
                  ORG_002_REASON, "Zoiko Steam"),
        sender=_sender_identity(SENDER_DEFAULT),
    )


# == DEV-002 Credential created ==========================================================
# Class A (Security) - Sender: Zoiko Steam Developer Platform - Recipients: the creator plus
# the Organization's administrators.
#
# This message exists as a DETECTION mechanism. An administrator who did not expect it must
# be able to tell, from the mail alone, who minted a credential, when, against which
# Organization, what it can reach, and where to go to revoke it.
#
# What it must never carry is the credential. The raw key is returned exactly once, in the
# creation response, and only its sha256 is stored - so the email identifies the credential
# by a hash-derived fingerprint instead. `prefix` is deliberately NOT used for that: it is
# raw[:12], which is eight characters of banner plus four characters of the real token.

DEV_002_SUBJECT = "A new Zoiko Steam API credential was created"
DEV_002_PREHEADER = "Review the fingerprint, scope, and creator."
DEV_002_HEADLINE = "A new API credential was created."
DEV_002_CTA = "Review API credentials"
DEV_002_SECRET_GUIDANCE = (
    "The secret is shown only at creation time. Store it in an approved secret manager and "
    "never place it in source code, chat, email, or client-side applications."
)
DEV_002_UNEXPECTED = (
    "If you did not expect this credential, revoke it from the Developer console and contact "
    "an authorized administrator of this Organization."
)


def api_credentials_url() -> str:
    """Customer-facing Developer console. Never a Super Admin surface, never a raw API."""
    return f"{public_base_url()}/organization/developer"


def send_api_credential_created_email(to, *, org_name, credential_name, fingerprint,
                                      created_at, creator, scope_summary, environment,
                                      expires_display, access_summary):
    """DEV-002. Every argument is safe metadata - there is no parameter that could carry
    the secret, which is the structural reason this template cannot leak one."""
    body_txt = (
        f"A new API credential for {org_name} was created at {created_at} with fingerprint "
        f"{fingerprint} and scopes {scope_summary}."
    )
    rows = [("Credential", credential_name), ("Organization", org_name),
            ("Fingerprint", fingerprint), ("Created", created_at),
            ("Created by", creator), ("Scopes", scope_summary),
            ("Permitted access", access_summary), ("Environment", environment),
            ("Expires", expires_display)]
    url = api_credentials_url()
    footer = f"{DEV_002_SECRET_GUIDANCE} {DEV_002_UNEXPECTED}"
    return _send(
        to,
        DEV_002_SUBJECT,
        _org_shell("API credential created", DEV_002_HEADLINE, DEV_002_PREHEADER, rows,
                   html.escape(body_txt), DEV_002_CTA, url, footer),
        _org_text(DEV_002_HEADLINE, DEV_002_PREHEADER, rows, body_txt, DEV_002_CTA, url,
                  footer, "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


# == DEV-003 Credential expiry ===========================================================
# Class A - Sender: Zoiko Steam Developer Platform.
#
# The DORMANCY variant is deliberately NOT built. Dormancy can only be asserted from real
# `last_used_at` telemetry, and no Zoiko Steam endpoint authenticates an API key, so nothing
# records a use. Inferring "dormant" from `created_at` would be asserting a fact about usage
# nobody measured. Reported as a gap instead.
#
# The expiry copy is likewise careful: it says the credential is MARKED expired, never that
# access was blocked, because no authentication path enforces it yet.

DEV_003_WARNING_SUBJECT = "Action required: Zoiko Steam API credential expires soon"
DEV_003_EXPIRED_SUBJECT = "Zoiko Steam API credential expired"
DEV_003_PREHEADER = "Review the credential and rotate it before it lapses."
DEV_003_CTA = "Rotate credential"
DEV_003_ROTATION_GUIDANCE = (
    "Create a replacement credential, move your integrations onto it, then revoke this one. "
    "The new secret is shown only once, at creation."
)


def send_credential_expiring_email(to, *, org_name, credential_name, fingerprint,
                                   expires_at, days_left, access_note):
    body_txt = (
        f"The API credential {credential_name} for {org_name} expires at {expires_at} "
        f"(in {days_left} days)."
    )
    rows = [("Credential", credential_name), ("Organization", org_name),
            ("Fingerprint", fingerprint), ("Expires", expires_at),
            ("Current state", "Active"), ("Effect on access", access_note)]
    url = api_credentials_url()
    return _send(
        to, DEV_003_WARNING_SUBJECT,
        _org_shell("Credential expiring", "An API credential expires soon.",
                   DEV_003_PREHEADER, rows, html.escape(body_txt), DEV_003_CTA, url,
                   DEV_003_ROTATION_GUIDANCE),
        _org_text("An API credential expires soon.", DEV_003_PREHEADER, rows, body_txt,
                  DEV_003_CTA, url, DEV_003_ROTATION_GUIDANCE,
                  "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


def send_credential_expired_email(to, *, org_name, credential_name, fingerprint,
                                  expired_at, access_note):
    body_txt = (
        f"The API credential {credential_name} for {org_name} reached its expiry at "
        f"{expired_at} and is now marked expired."
    )
    rows = [("Credential", credential_name), ("Organization", org_name),
            ("Fingerprint", fingerprint), ("Expired", expired_at),
            ("Current state", "Expired"), ("Effect on access", access_note)]
    url = api_credentials_url()
    return _send(
        to, DEV_003_EXPIRED_SUBJECT,
        _org_shell("Credential expired", "An API credential has expired.",
                   DEV_003_PREHEADER, rows, html.escape(body_txt), DEV_003_CTA, url,
                   DEV_003_ROTATION_GUIDANCE),
        _org_text("An API credential has expired.", DEV_003_PREHEADER, rows, body_txt,
                  DEV_003_CTA, url, DEV_003_ROTATION_GUIDANCE,
                  "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


# == DEV-004 Credential rotation and revocation ==========================================
# Class A - Sender: Zoiko Steam Developer Platform. Never carries either secret.

DEV_004_REVOKED_SUBJECT = "A Zoiko Steam API credential was revoked"
DEV_004_ROTATED_SUBJECT = "A Zoiko Steam API credential was rotated"
DEV_004_EMERGENCY_SUBJECT = "Important: Zoiko Steam API credential was revoked for security"
DEV_004_PREHEADER = "Review the credential and who changed it."
DEV_004_CTA = "Review API credentials"


def send_credential_revoked_email(to, *, org_name, credential_name, fingerprint,
                                  revoked_at, revoked_by, scope_summary):
    body_txt = (
        f"The API credential {credential_name} for {org_name} was revoked at {revoked_at} "
        f"by {revoked_by}."
    )
    rows = [("Credential", credential_name), ("Organization", org_name),
            ("Fingerprint", fingerprint), ("Revoked", revoked_at),
            ("Revoked by", revoked_by), ("Previous scopes", scope_summary),
            ("Current state", "Revoked")]
    url = api_credentials_url()
    footer = ("If you did not expect this, contact an authorized administrator of this "
              "Organization.")
    return _send(
        to, DEV_004_REVOKED_SUBJECT,
        _org_shell("Credential revoked", "An API credential was revoked.",
                   DEV_004_PREHEADER, rows, html.escape(body_txt), DEV_004_CTA, url, footer),
        _org_text("An API credential was revoked.", DEV_004_PREHEADER, rows, body_txt,
                  DEV_004_CTA, url, footer, "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


def send_credential_rotated_email(to, *, org_name, credential_name, old_fingerprint,
                                  new_fingerprint, rotated_at, rotated_by, overlap_note):
    body_txt = (
        f"The API credential {credential_name} for {org_name} was rotated at {rotated_at} "
        f"by {rotated_by}. The replacement carries fingerprint {new_fingerprint}."
    )
    rows = [("Credential", credential_name), ("Organization", org_name),
            ("Previous fingerprint", old_fingerprint),
            ("New fingerprint", new_fingerprint),
            ("Rotated", rotated_at), ("Rotated by", rotated_by),
            ("Previous credential", overlap_note)]
    url = api_credentials_url()
    footer = ("The replacement secret was shown once, at rotation. Store it in an approved "
              "secret manager.")
    return _send(
        to, DEV_004_ROTATED_SUBJECT,
        _org_shell("Credential rotated", "An API credential was rotated.",
                   DEV_004_PREHEADER, rows, html.escape(body_txt), DEV_004_CTA, url, footer),
        _org_text("An API credential was rotated.", DEV_004_PREHEADER, rows, body_txt,
                  DEV_004_CTA, url, footer, "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


def send_credential_emergency_revoked_email(to, *, org_name, credential_name, fingerprint,
                                            revoked_at, reason_label, next_steps):
    body_txt = (
        f"The API credential {credential_name} for {org_name} was revoked at {revoked_at} "
        f"as a security measure."
    )
    rows = [("Credential", credential_name), ("Organization", org_name),
            ("Fingerprint", fingerprint), ("Effective", revoked_at),
            ("Reason", reason_label), ("Current state", "Revoked")]
    url = api_credentials_url()
    return _send(
        to, DEV_004_EMERGENCY_SUBJECT,
        _org_shell("Credential revoked", "An API credential was revoked for security.",
                   "Review this credential now.", rows, html.escape(body_txt),
                   "Review credential security", url, next_steps),
        _org_text("An API credential was revoked for security.", "Review this credential now.",
                  rows, body_txt, "Review credential security", url, next_steps,
                  "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


# == DEV-006 Webhook endpoint verification ===============================================
# Class A - production events are withheld until the endpoint proves control of the URL.

DEV_006_VERIFY_SUBJECT = "Verify your Zoiko Steam webhook endpoint"
DEV_006_RESET_SUBJECT = "Webhook verification was reset"
DEV_006_PREHEADER = "Production events are withheld until this endpoint is verified."
DEV_006_CTA = "Review webhook endpoint"
DEV_006_WITHHELD = (
    "No production events will be delivered to this endpoint until verification succeeds."
)


def webhook_endpoints_url() -> str:
    return f"{public_base_url()}/organization/developer"


def send_webhook_verification_email(to, *, org_name, endpoint_url, expires_at,
                                    challenge_header):
    body_txt = (
        f"The webhook endpoint {endpoint_url} for {org_name} must prove control of that URL "
        f"before it can receive production events."
    )
    rows = [("Organization", org_name), ("Endpoint", endpoint_url),
            ("Status", "Pending verification"),
            ("Verification expires", expires_at),
            ("Challenge header", challenge_header)]
    url = webhook_endpoints_url()
    return _send(
        to, DEV_006_VERIFY_SUBJECT,
        _org_shell("Verify webhook endpoint", "This webhook endpoint needs verification.",
                   DEV_006_PREHEADER, rows, html.escape(body_txt), DEV_006_CTA, url,
                   DEV_006_WITHHELD),
        _org_text("This webhook endpoint needs verification.", DEV_006_PREHEADER, rows,
                  body_txt, DEV_006_CTA, url, DEV_006_WITHHELD,
                  "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


def send_webhook_verification_reset_email(to, *, org_name, endpoint_url, reset_at, reason):
    body_txt = (
        f"Verification for the webhook endpoint {endpoint_url} in {org_name} was reset at "
        f"{reset_at}."
    )
    rows = [("Organization", org_name), ("Endpoint", endpoint_url),
            ("Reset", reset_at), ("Reason", reason),
            ("Status", "Pending verification")]
    url = webhook_endpoints_url()
    return _send(
        to, DEV_006_RESET_SUBJECT,
        _org_shell("Verification reset", "Webhook verification was reset.",
                   DEV_006_PREHEADER, rows, html.escape(body_txt), DEV_006_CTA, url,
                   DEV_006_WITHHELD),
        _org_text("Webhook verification was reset.", DEV_006_PREHEADER, rows, body_txt,
                  DEV_006_CTA, url, DEV_006_WITHHELD, "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


# == DEV-007 Webhook delivery health =====================================================
# One notification per health-state TRANSITION, never one per retry attempt.

DEV_007_FAILING_SUBJECT = "Action required: Zoiko Steam webhook delivery is failing"
DEV_007_RECOVERED_SUBJECT = "Zoiko Steam webhook delivery recovered"
DEV_007_DISABLED_SUBJECT = "Zoiko Steam webhook endpoint was disabled"
DEV_007_PREHEADER = "Review the endpoint and its recent delivery outcomes."
DEV_007_CTA = "Review webhook delivery"


def send_webhook_degraded_email(to, *, org_name, endpoint_url, consecutive_failures,
                                failure_window, last_success, next_retry, dead_lettered):
    body_txt = (
        f"Deliveries to {endpoint_url} in {org_name} have failed {consecutive_failures} "
        f"times in a row."
    )
    rows = [("Organization", org_name), ("Endpoint", endpoint_url),
            ("Consecutive failures", str(consecutive_failures)),
            ("Failure window", failure_window),
            ("Last successful delivery", last_success),
            ("Current state", "Degraded"), ("Next retry", next_retry),
            ("Awaiting replay", str(dead_lettered))]
    url = webhook_endpoints_url()
    footer = ("Events are retained while retries continue. Response bodies are not included "
              "in this notice.")
    return _send(
        to, DEV_007_FAILING_SUBJECT,
        _org_shell("Delivery failing", "Webhook delivery is failing.", DEV_007_PREHEADER,
                   rows, html.escape(body_txt), DEV_007_CTA, url, footer),
        _org_text("Webhook delivery is failing.", DEV_007_PREHEADER, rows, body_txt,
                  DEV_007_CTA, url, footer, "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


def send_webhook_recovered_email(to, *, org_name, endpoint_url, recovered_at,
                                 prior_failure_window, dead_lettered):
    body_txt = (
        f"Deliveries to {endpoint_url} in {org_name} resumed successfully at {recovered_at}."
    )
    rows = [("Organization", org_name), ("Endpoint", endpoint_url),
            ("Recovered", recovered_at), ("Prior failure window", prior_failure_window),
            ("Current state", "Healthy"),
            ("Still awaiting replay", str(dead_lettered))]
    url = webhook_endpoints_url()
    footer = ("Events that exhausted their retries during the outage are retained and can be "
              "replayed from the Developer console.")
    return _send(
        to, DEV_007_RECOVERED_SUBJECT,
        _org_shell("Delivery recovered", "Webhook delivery has recovered.",
                   DEV_007_PREHEADER, rows, html.escape(body_txt), DEV_007_CTA, url, footer),
        _org_text("Webhook delivery has recovered.", DEV_007_PREHEADER, rows, body_txt,
                  DEV_007_CTA, url, footer, "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


def send_webhook_disabled_email(to, *, org_name, endpoint_url, disabled_at, reason_label,
                                future_events, reenable_route):
    body_txt = (
        f"The webhook endpoint {endpoint_url} in {org_name} was disabled at {disabled_at} "
        f"after repeated delivery failures."
    )
    rows = [("Organization", org_name), ("Endpoint", endpoint_url),
            ("Disabled", disabled_at), ("Reason", reason_label),
            ("Current state", "Disabled"), ("Future events", future_events)]
    url = webhook_endpoints_url()
    return _send(
        to, DEV_007_DISABLED_SUBJECT,
        _org_shell("Endpoint disabled", "A webhook endpoint was disabled.",
                   DEV_007_PREHEADER, rows, html.escape(body_txt), DEV_007_CTA, url,
                   reenable_route),
        _org_text("A webhook endpoint was disabled.", DEV_007_PREHEADER, rows, body_txt,
                  DEV_007_CTA, url, reenable_route, "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


# == DEV-008 Webhook signing-secret rotation =============================================
# Class A - Sender: Zoiko Steam Developer Platform. Fingerprints only, never secrets.

DEV_008_STARTED_SUBJECT = "Zoiko Steam webhook signing secret rotation started"
DEV_008_ENDING_SUBJECT = "Action required: webhook signing-secret rotation ends soon"
DEV_008_PREHEADER = "Update your integration before the overlap window closes."
DEV_008_CTA = "Review webhook signing"


def send_signing_rotation_started_email(to, *, org_name, endpoint_url, old_fingerprint,
                                        new_fingerprint, started_at, overlap_ends_at,
                                        required_action):
    body_txt = (
        f"A new signing secret was issued for {endpoint_url} in {org_name} at {started_at}. "
        f"Both secrets sign every delivery until {overlap_ends_at}."
    )
    rows = [("Organization", org_name), ("Endpoint", endpoint_url),
            ("Previous fingerprint", old_fingerprint),
            ("New fingerprint", new_fingerprint),
            ("Rotation started", started_at), ("Overlap ends", overlap_ends_at),
            ("Required action", required_action)]
    url = webhook_endpoints_url()
    footer = ("The secret itself is shown only once, in the Developer console. It is never "
              "sent by email.")
    return _send(
        to, DEV_008_STARTED_SUBJECT,
        _org_shell("Signing rotation", "A webhook signing secret was rotated.",
                   DEV_008_PREHEADER, rows, html.escape(body_txt), DEV_008_CTA, url, footer),
        _org_text("A webhook signing secret was rotated.", DEV_008_PREHEADER, rows, body_txt,
                  DEV_008_CTA, url, footer, "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


def send_signing_rotation_ending_email(to, *, org_name, endpoint_url, new_fingerprint,
                                       overlap_ends_at, hours_left):
    body_txt = (
        f"The signing-secret overlap for {endpoint_url} in {org_name} ends at "
        f"{overlap_ends_at}, in about {hours_left} hours."
    )
    rows = [("Organization", org_name), ("Endpoint", endpoint_url),
            ("New fingerprint", new_fingerprint), ("Overlap ends", overlap_ends_at),
            ("After the deadline", "The previous secret stops signing deliveries")]
    url = webhook_endpoints_url()
    footer = ("Verify with the new signing secret before the deadline or signature checks "
              "will start failing.")
    return _send(
        to, DEV_008_ENDING_SUBJECT,
        _org_shell("Rotation ending", "A signing-secret rotation is about to close.",
                   DEV_008_PREHEADER, rows, html.escape(body_txt), DEV_008_CTA, url, footer),
        _org_text("A signing-secret rotation is about to close.", DEV_008_PREHEADER, rows,
                  body_txt, DEV_008_CTA, url, footer, "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


# == DEV-010 Rate-limit and API anomaly ==================================================
# Sent only from a durable, committed threshold event - never from a counter in memory.

DEV_010_SUBJECT = "Zoiko Steam API usage requires attention"
DEV_010_PREHEADER = "Review the affected integration and the observed window."
DEV_010_CTA = "Review API usage"


def send_api_usage_email(to, *, org_name, rule_label, observed_window, observed_count,
                         threshold, current_state, resets_at, recommended_action):
    body_txt = (
        f"Sustained rate-limited activity was recorded for {org_name} against "
        f"{rule_label} during {observed_window}."
    )
    rows = [("Organization", org_name), ("Affected API", rule_label),
            ("Observed window", observed_window),
            ("Requests refused", str(observed_count)),
            ("Threshold category", threshold),
            ("Current state", current_state), ("Resets", resets_at),
            ("Recommended action", recommended_action)]
    url = api_credentials_url()
    footer = ("Zoiko Steam does not disclose exact anti-abuse thresholds. Contact support if "
              "you expect this level of traffic.")
    return _send(
        to, DEV_010_SUBJECT,
        _org_shell("API usage", "Your API usage needs attention.", DEV_010_PREHEADER, rows,
                   html.escape(body_txt), DEV_010_CTA, url, footer),
        _org_text("Your API usage needs attention.", DEV_010_PREHEADER, rows, body_txt,
                  DEV_010_CTA, url, footer, "Zoiko Steam Developer Platform"),
        sender=_sender_identity(SENDER_DEVELOPER),
    )


# == DEV-012 Developer data export =======================================================
# Class C transactional. The download link is short-lived, purpose-bound and carries no PII.

DEV_012_READY_SUBJECT = "Your Zoiko Steam developer data export is ready"
DEV_012_EXPIRED_SUBJECT = "Your Zoiko Steam developer data export expired"
DEV_012_FAILED_SUBJECT = "Zoiko Steam could not generate your developer data export"
DEV_012_PREHEADER = "Download it before the link expires."
DEV_012_CTA = "Download export"
DEV_012_NO_SECRETS = (
    "Exports contain metadata only. API key secrets, webhook signing secrets and tokens are "
    "never included."
)


def developer_exports_url() -> str:
    return f"{public_base_url()}/organization/developer"


def send_export_ready_email(to, *, org_name, export_type, requested_at, completed_at,
                            expires_at, download_url):
    body_txt = (
        f"The {export_type} export for {org_name} requested at {requested_at} is ready. "
        f"The download link expires at {expires_at}."
    )
    rows = [("Organization", org_name), ("Export", export_type),
            ("Requested", requested_at), ("Completed", completed_at),
            ("Link expires", expires_at)]
    return _send(
        to, DEV_012_READY_SUBJECT,
        _org_shell("Export ready", "Your developer data export is ready.",
                   DEV_012_PREHEADER, rows, html.escape(body_txt), DEV_012_CTA,
                   download_url, DEV_012_NO_SECRETS),
        _org_text("Your developer data export is ready.", DEV_012_PREHEADER, rows, body_txt,
                  DEV_012_CTA, download_url, DEV_012_NO_SECRETS, "Zoiko Steam"),
        sender=_sender_identity(SENDER_DEFAULT),
    )


def send_export_expired_email(to, *, org_name, export_type, expired_at):
    body_txt = (
        f"The {export_type} export for {org_name} expired at {expired_at} and can no longer "
        f"be downloaded."
    )
    rows = [("Organization", org_name), ("Export", export_type), ("Expired", expired_at),
            ("Current state", "Expired")]
    url = developer_exports_url()
    return _send(
        to, DEV_012_EXPIRED_SUBJECT,
        _org_shell("Export expired", "Your developer data export expired.",
                   "Create a new export if you still need it.", rows, html.escape(body_txt),
                   "Create a new export", url, DEV_012_NO_SECRETS),
        _org_text("Your developer data export expired.",
                  "Create a new export if you still need it.", rows, body_txt,
                  "Create a new export", url, DEV_012_NO_SECRETS, "Zoiko Steam"),
        sender=_sender_identity(SENDER_DEFAULT),
    )


def send_export_failed_email(to, *, org_name, export_type, export_reference, failure_category):
    body_txt = (
        f"Zoiko Steam could not generate the {export_type} export for {org_name}."
    )
    rows = [("Organization", org_name), ("Export", export_type),
            ("Reference", export_reference), ("Reason", failure_category),
            ("Current state", "Failed")]
    url = developer_exports_url()
    footer = "Try again from the Developer console, or contact Zoiko Steam Support."
    return _send(
        to, DEV_012_FAILED_SUBJECT,
        _org_shell("Export failed", "Your developer data export could not be generated.",
                   "No data was produced.", rows, html.escape(body_txt),
                   "Create a new export", url, footer),
        _org_text("Your developer data export could not be generated.",
                  "No data was produced.", rows, body_txt, "Create a new export", url,
                  footer, "Zoiko Steam"),
        sender=_sender_identity(SENDER_DEFAULT),
    )


# == MED-001 / MED-002 / MED-004 / MED-005 — media operations ============================
# Operator-facing, Sender: Zoiko Steam Media Operations. Every one of these reports
# committed media state; none of them is an audience communication.
#
# `[TEST MODE]` comes from Organization.is_test — a real stored flag, the same one
# services/ops.py uses to keep test orgs out of the live console. It is never inferred from
# a hostname, an input name, a developer account or a stream-key prefix.
#
# No template here accepts a stream key, ingest credential or LiveKit token. There is no
# parameter that could carry one, which is the structural reason none can leak.

SENDER_MEDIA = "Zoiko Steam Media Operations"
TEST_MODE_PREFIX = "[TEST MODE] "


def media_subject(subject: str, test_mode: bool) -> str:
    """Prefix a media subject when the Organization is authoritatively a test tenant."""
    return f"{TEST_MODE_PREFIX}{subject}" if test_mode else subject


def live_inputs_url() -> str:
    return f"{public_base_url()}/organization/events"


def live_sessions_url() -> str:
    return f"{public_base_url()}/organization/events"


# ── MED-001 Live input created ──────────────────────────────────────────────────────────

MED_001_SUBJECT = "Live input created in Zoiko Steam"
MED_001_PREHEADER = "Review the protocol, region, and owner."
MED_001_HEADLINE = "Your live input is ready."
MED_001_CTA = "Review live input"
MED_001_NO_CREDENTIALS = (
    "Ingest credentials are never sent by email. Reveal the stream key from the event's "
    "live inputs panel when you need it."
)


def send_live_input_created_email(to, *, event_title, input_name, owner, org_name,
                                  workspace, protocol, region, mode, created_at,
                                  enforced_note, test_mode=False):
    body_txt = (
        f"The live input {input_name} for {event_title} was created at {created_at} using "
        f"{protocol}."
    )
    rows = [("Input", input_name), ("Event", event_title), ("Organization", org_name),
            ("Workspace", workspace), ("Owner", owner), ("Protocol", protocol),
            ("Region", region), ("Mode", mode), ("Created", created_at),
            ("Ingest status", enforced_note)]
    url = live_inputs_url()
    return _send(
        to, media_subject(MED_001_SUBJECT, test_mode),
        _org_shell("Live input created", MED_001_HEADLINE, MED_001_PREHEADER, rows,
                   html.escape(body_txt), MED_001_CTA, url, MED_001_NO_CREDENTIALS),
        _org_text(MED_001_HEADLINE, MED_001_PREHEADER, rows, body_txt, MED_001_CTA, url,
                  MED_001_NO_CREDENTIALS, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


# ── MED-002 Input interruption and recovery ─────────────────────────────────────────────

MED_002_INTERRUPTED_SUBJECT = "Action required: Zoiko Steam live input was interrupted"
MED_002_INTERMITTENT_SUBJECT = "Zoiko Steam live input is intermittent"
MED_002_RECOVERED_SUBJECT = "Zoiko Steam live input recovered"
MED_002_PREHEADER = "Review the input and its current ingest state."
MED_002_CTA = "Review live input"


def send_input_interrupted_email(to, *, event_title, input_name, interrupted_at,
                                 persistence, current_state, org_name, test_mode=False):
    body_txt = (
        f"The live input {input_name} for {event_title} lost usable signal at "
        f"{interrupted_at} and has stayed down for {persistence}."
    )
    rows = [("Input", input_name), ("Event", event_title), ("Organization", org_name),
            ("Interruption started", interrupted_at), ("Confirmed after", persistence),
            ("Current ingest state", current_state)]
    url = live_inputs_url()
    footer = ("Check the encoder, its network path, and that it is still publishing to the "
              "configured ingest URL.")
    return _send(
        to, media_subject(MED_002_INTERRUPTED_SUBJECT, test_mode),
        _org_shell("Input interrupted", "A live input lost usable signal.",
                   MED_002_PREHEADER, rows, html.escape(body_txt), MED_002_CTA, url, footer),
        _org_text("A live input lost usable signal.", MED_002_PREHEADER, rows, body_txt,
                  MED_002_CTA, url, footer, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


def send_input_intermittent_email(to, *, event_title, input_name, window, interruptions,
                                  current_state, org_name, test_mode=False):
    body_txt = (
        f"The live input {input_name} for {event_title} has been interrupted "
        f"{interruptions} times in the last {window}."
    )
    rows = [("Input", input_name), ("Event", event_title), ("Organization", org_name),
            ("Observation window", window),
            ("Confirmed interruptions", str(interruptions)),
            ("Current ingest state", current_state)]
    url = live_inputs_url()
    footer = ("A repeating drop usually means upstream packet loss or an encoder restarting. "
              "Check the contribution path before the next session.")
    return _send(
        to, media_subject(MED_002_INTERMITTENT_SUBJECT, test_mode),
        _org_shell("Input intermittent", "A live input is dropping repeatedly.",
                   MED_002_PREHEADER, rows, html.escape(body_txt), MED_002_CTA, url, footer),
        _org_text("A live input is dropping repeatedly.", MED_002_PREHEADER, rows, body_txt,
                  MED_002_CTA, url, footer, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


def send_input_recovered_email(to, *, event_title, input_name, recovered_at, outage,
                               current_state, org_name, test_mode=False):
    body_txt = (
        f"The live input {input_name} for {event_title} regained a stable signal at "
        f"{recovered_at} after {outage}."
    )
    rows = [("Input", input_name), ("Event", event_title), ("Organization", org_name),
            ("Recovered", recovered_at), ("Outage duration", outage),
            ("Current ingest state", current_state)]
    url = live_inputs_url()
    return _send(
        to, media_subject(MED_002_RECOVERED_SUBJECT, test_mode),
        _org_shell("Input recovered", "A live input recovered.", MED_002_PREHEADER, rows,
                   html.escape(body_txt), MED_002_CTA, url,
                   "Signal has been stable long enough to be considered recovered."),
        _org_text("A live input recovered.", MED_002_PREHEADER, rows, body_txt,
                  MED_002_CTA, url,
                  "Signal has been stable long enough to be considered recovered.",
                  "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


# ── MED-004 Streaming session lifecycle ─────────────────────────────────────────────────
# INTERNAL operator communication. Starting a broadcast session must never reach an
# audience — see services/media_comms.py, which has no access to any audience sender.

MED_004_STARTED_SUBJECT = "Zoiko Steam streaming session started"
MED_004_ENDED_SUBJECT = "Zoiko Steam streaming session ended"
MED_004_PREHEADER = "Internal operations notice for this session."
MED_004_CTA = "Open the operations view"


def send_session_started_email(to, *, event_title, session_reference, started_at,
                               active_input, region, mode, org_name, test_mode=False):
    body_txt = (
        f"The streaming session for {event_title} went live at {started_at}."
    )
    rows = [("Event", event_title), ("Session reference", session_reference),
            ("Organization", org_name), ("Started", started_at),
            ("Active input", active_input), ("Region", region), ("Mode", mode)]
    url = live_sessions_url()
    footer = "This is an internal operations notice. No audience communication was sent."
    return _send(
        to, media_subject(MED_004_STARTED_SUBJECT, test_mode),
        _org_shell("Session started", "A streaming session is live.", MED_004_PREHEADER,
                   rows, html.escape(body_txt), MED_004_CTA, url, footer),
        _org_text("A streaming session is live.", MED_004_PREHEADER, rows, body_txt,
                  MED_004_CTA, url, footer, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


def send_session_ended_email(to, *, event_title, session_reference, started_at, ended_at,
                             duration, final_state, recording_status, peak_viewers,
                             org_name, test_mode=False):
    body_txt = (
        f"The streaming session for {event_title} ended at {ended_at} after {duration}."
    )
    rows = [("Event", event_title), ("Session reference", session_reference),
            ("Organization", org_name), ("Started", started_at), ("Ended", ended_at),
            ("Duration", duration), ("Final state", final_state),
            ("Recording", recording_status), ("Peak viewers", str(peak_viewers))]
    url = live_sessions_url()
    footer = "This is an internal operations notice. No audience communication was sent."
    return _send(
        to, media_subject(MED_004_ENDED_SUBJECT, test_mode),
        _org_shell("Session ended", "A streaming session has ended.", MED_004_PREHEADER,
                   rows, html.escape(body_txt), MED_004_CTA, url, footer),
        _org_text("A streaming session has ended.", MED_004_PREHEADER, rows, body_txt,
                  MED_004_CTA, url, footer, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


# ── MED-005 Streaming-session health ────────────────────────────────────────────────────

MED_005_FAILED_SUBJECT = "Action required: Zoiko Steam streaming session failed"
MED_005_DEGRADED_SUBJECT = "Zoiko Steam streaming session is degraded"
MED_005_RECOVERED_SUBJECT = "Zoiko Steam streaming session recovered"
MED_005_PREHEADER = "Review the session's current health."
MED_005_CTA = "Open the operations view"


def send_session_health_email(to, *, variant, event_title, session_reference, confirmed_at,
                              issues, previous_state, current_state, impact, org_name,
                              incident_duration=None, test_mode=False):
    """One function, three variants — the facts are identical, only the framing differs.

    `issues` comes straight from services/broadcast.health_of(); this template never
    re-derives health, it only renders what that calculation returned.
    """
    subjects = {"failed": MED_005_FAILED_SUBJECT,
                "degraded": MED_005_DEGRADED_SUBJECT,
                "recovered": MED_005_RECOVERED_SUBJECT}
    headlines = {"failed": "A streaming session failed.",
                 "degraded": "A streaming session is degraded.",
                 "recovered": "A streaming session recovered."}
    subject = subjects.get(variant, MED_005_DEGRADED_SUBJECT)
    headline = headlines.get(variant, headlines["degraded"])

    body_txt = (
        f"The streaming session for {event_title} moved from {previous_state} to "
        f"{current_state} at {confirmed_at}."
    )
    rows = [("Event", event_title), ("Session reference", session_reference),
            ("Organization", org_name), ("Confirmed", confirmed_at),
            ("Previous state", previous_state), ("Current state", current_state),
            ("Observed issues", issues or "None"), ("Impact", impact)]
    if incident_duration:
        rows.append(("Incident duration", incident_duration))
    url = live_sessions_url()
    footer = ("Health is derived from what the platform observes: publishing state, "
              "participant connection quality, and recording capture.")
    return _send(
        to, media_subject(subject, test_mode),
        _org_shell("Session health", headline, MED_005_PREHEADER, rows,
                   html.escape(body_txt), MED_005_CTA, url, footer),
        _org_text(headline, MED_005_PREHEADER, rows, body_txt, MED_005_CTA, url, footer,
                  "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


# == MED-007 Recording health lifecycle ==================================================
# Operator + asset-owner facing. Every fact here is read from committed LiveRecording rows.
#
# MED-006 (media failover) has NO templates in this file, deliberately. There is no
# secondary media path, no path state and no failover controller anywhere in this codebase,
# so any subject line about a "secondary media path" would describe a capability that does
# not exist. See services/media_comms.py's module docstring and test_media_governance.py.
#
# The word "replay" does not appear in a MED-007 template. A recording that stopped is not a
# recording that validated, and a recording that validated is not a published replay — those
# are MED-008 and MED-009 respectively, and collapsing them is exactly how an operator ends
# up telling a customer a replay is ready before anything checked the file.

MED_007_STARTED_SUBJECT = "Zoiko Steam recording started"
MED_007_DEGRADED_SUBJECT = "Action required: Zoiko Steam recording health is degraded"
MED_007_RECOVERED_SUBJECT = "Zoiko Steam recording recovered"
MED_007_STOPPED_SUBJECT = "Zoiko Steam recording stopped"
MED_007_PREHEADER = "Review the recording and its current capture state."
MED_007_CTA = "Review recording"

# Stated on every MED-007 message so the boundary is impossible to miss.
MED_007_NOT_REPLAY = (
    "Capture state is not replay availability. A recording is validated separately, and "
    "publishing a replay is a separate operator decision after that."
)
MED_007_NO_CREDENTIALS = (
    "Storage locations and provider credentials are never sent by email."
)


def recordings_url():
    return f"{public_base_url()}/organization/events"


def send_recording_started_email(to, *, event_title, recording_reference, started_at,
                                 owner, org_name, recording_mode, redundancy, quality,
                                 capture_status, test_mode=False):
    """MED-007 Started. Sent only once a capture row is committed as `recording`.

    `redundancy` is the authoritative independent-recording answer, taken from the event's
    commercial service profile (crud.commercial.dual_recording_required) — never a guess
    about whether a second path "should" exist.
    """
    body_txt = (
        f"Recording {recording_reference} for {event_title} started at {started_at}."
    )
    rows = [("Recording", recording_reference), ("Event", event_title),
            ("Organization", org_name), ("Asset owner", owner), ("Started", started_at),
            ("Recording mode", recording_mode), ("Quality", quality),
            ("Independent recording", redundancy), ("Capture status", capture_status)]
    url = recordings_url()
    return _send(
        to, media_subject(MED_007_STARTED_SUBJECT, test_mode),
        _org_shell("Recording started", "A recording has started.", MED_007_PREHEADER, rows,
                   html.escape(body_txt), MED_007_CTA, url, MED_007_NOT_REPLAY),
        _org_text("A recording has started.", MED_007_PREHEADER, rows, body_txt,
                  MED_007_CTA, url, MED_007_NOT_REPLAY, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


def send_recording_degraded_email(to, *, event_title, recording_reference, degraded_at,
                                  affected_component, capture_state, operator_action,
                                  org_name, impact_class, test_mode=False):
    """MED-007 Degraded. Only ever sent from a governed degradation state.

    `affected_component` names the capture path that is not healthy — "Secondary recording
    path", not a provider error string. `impact_class` carries the event's stored blast
    radius (Event.impact), which is why an unrepeatable event reads differently here.
    """
    body_txt = (
        f"Recording {recording_reference} for {event_title} became degraded at "
        f"{degraded_at}."
    )
    rows = [("Recording", recording_reference), ("Event", event_title),
            ("Organization", org_name), ("Degradation started", degraded_at),
            ("Affected component", affected_component),
            ("Current capture state", capture_state),
            ("Event impact class", impact_class),
            ("Operator action", operator_action)]
    url = recordings_url()
    return _send(
        to, media_subject(MED_007_DEGRADED_SUBJECT, test_mode),
        _org_shell("Recording degraded", "Recording health is degraded.",
                   MED_007_PREHEADER, rows, html.escape(body_txt), MED_007_CTA, url,
                   MED_007_NOT_REPLAY),
        _org_text("Recording health is degraded.", MED_007_PREHEADER, rows, body_txt,
                  MED_007_CTA, url, MED_007_NOT_REPLAY, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


def send_recording_recovered_email(to, *, event_title, recording_reference, recovered_at,
                                   incident_duration, remaining_risk, capture_state,
                                   org_name, test_mode=False):
    body_txt = (
        f"Recording {recording_reference} for {event_title} returned to a healthy capture "
        f"state at {recovered_at}."
    )
    rows = [("Recording", recording_reference), ("Event", event_title),
            ("Organization", org_name), ("Recovered", recovered_at),
            ("Incident duration", incident_duration),
            ("Current capture state", capture_state),
            ("Remaining risk", remaining_risk)]
    url = recordings_url()
    return _send(
        to, media_subject(MED_007_RECOVERED_SUBJECT, test_mode),
        _org_shell("Recording recovered", "Recording health recovered.", MED_007_PREHEADER,
                   rows, html.escape(body_txt), MED_007_CTA, url, MED_007_NOT_REPLAY),
        _org_text("Recording health recovered.", MED_007_PREHEADER, rows, body_txt,
                  MED_007_CTA, url, MED_007_NOT_REPLAY, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


def send_recording_stopped_email(to, *, event_title, recording_reference, stopped_at,
                                 duration, final_capture_state, next_step, org_name,
                                 test_mode=False):
    """MED-007 Stopped. `next_step` states that validation has not run yet — this template
    must never imply a replay is ready, which is what MED-008 exists to say afterwards."""
    body_txt = (
        f"Recording {recording_reference} for {event_title} stopped at {stopped_at} after "
        f"{duration}."
    )
    rows = [("Recording", recording_reference), ("Event", event_title),
            ("Organization", org_name), ("Stopped", stopped_at),
            ("Recording duration", duration),
            ("Final capture state", final_capture_state), ("Next step", next_step)]
    url = recordings_url()
    return _send(
        to, media_subject(MED_007_STOPPED_SUBJECT, test_mode),
        _org_shell("Recording stopped", "A recording has stopped.", MED_007_PREHEADER, rows,
                   html.escape(body_txt), MED_007_CTA, url, MED_007_NOT_REPLAY),
        _org_text("A recording has stopped.", MED_007_PREHEADER, rows, body_txt,
                  MED_007_CTA, url, MED_007_NOT_REPLAY, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


MED_007_REDUNDANCY_SUBJECT = "Action required: Zoiko Steam independent recording is missing"


def send_recording_redundancy_email(to, *, event_title, recording_reference, detected_at,
                                    required_paths, capturing_paths, impact_class,
                                    org_name, test_mode=False):
    """The unrepeatable-event control. Sent when an event whose service profile REQUIRES two
    independent recording paths is capturing on fewer than that.

    Both halves of this are real stored state: the requirement comes from the event's
    commercial service profile, and the count comes from LiveRecording rows with
    `enforced=True`. Nothing here is inferred from an event's name or its importance.
    """
    body_txt = (
        f"{event_title} requires {required_paths} independent recording paths but only "
        f"{capturing_paths} are capturing."
    )
    rows = [("Recording", recording_reference), ("Event", event_title),
            ("Organization", org_name), ("Detected", detected_at),
            ("Event impact class", impact_class),
            ("Required independent paths", str(required_paths)),
            ("Paths actually capturing", str(capturing_paths))]
    url = recordings_url()
    footer = ("This event's service profile requires independent recording. Restore the "
              "second path before the event continues, or record a single-path override.")
    return _send(
        to, media_subject(MED_007_REDUNDANCY_SUBJECT, test_mode),
        _org_shell("Independent recording missing", "Required recording redundancy is missing.",
                   MED_007_PREHEADER, rows, html.escape(body_txt), MED_007_CTA, url, footer),
        _org_text("Required recording redundancy is missing.", MED_007_PREHEADER, rows,
                  body_txt, MED_007_CTA, url, footer, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


# == MED-008 Recording finalization ======================================================
# Sent when VALIDATION completes — not when recording stops. services/validation.py owns the
# checks; these templates only report what it recorded.
#
# `validated_components` is built from the actual evidence dict, so it can only ever list
# checks that genuinely ran. There is no cryptographic integrity verification in this
# platform and no template here claims one: the checks are object existence, readability,
# duration plausibility, and audio/video track presence, plus a duration comparison between
# paths when the event records two.

MED_008_READY_SUBJECT = "Zoiko Steam recording validation completed"
MED_008_PARTIAL_SUBJECT = "Zoiko Steam recording completed with partial media"
MED_008_FAILED_SUBJECT = "Action required: Zoiko Steam recording finalization failed"
MED_008_RECOVERED_SUBJECT = "Zoiko Steam recording asset was recovered"
MED_008_PREHEADER = "Review the validation result for this recording."
MED_008_CTA = "Review recording"

# The single most important sentence in this family. Validation says the FILE is good;
# it says nothing about whether anyone may watch it.
MED_008_REPLAY_SEPARATION = (
    "Validation is not publication. Replay availability is decided separately and is "
    "reported by its own notification when a replay is actually published."
)


def send_recording_finalized_email(to, *, variant, event_title, recording_reference,
                                   finalized_at, validated_components, not_checked,
                                   duration, asset_status, replay_status, org_name,
                                   missing_components=None, remediation=None,
                                   failure_category=None, recovered_components=None,
                                   test_mode=False):
    """One function, four variants (ready | partial | failed | recovered).

    `replay_status` is read from the event's ReplayEntitlement.publish_state, never assumed
    — a validated recording whose replay was never published must say so.

    `failure_category` is a safe category, never a stack trace, a bucket path or a provider
    exception. The caller resolves it from a closed set.
    """
    subjects = {"ready": MED_008_READY_SUBJECT, "partial": MED_008_PARTIAL_SUBJECT,
                "failed": MED_008_FAILED_SUBJECT, "recovered": MED_008_RECOVERED_SUBJECT}
    headlines = {"ready": "Recording validation completed.",
                 "partial": "This recording completed with partial media.",
                 "failed": "Recording finalization failed.",
                 "recovered": "A recording asset was recovered."}
    titles = {"ready": "Validation completed", "partial": "Partial media",
              "failed": "Finalization failed", "recovered": "Asset recovered"}
    subject = subjects.get(variant, MED_008_READY_SUBJECT)
    headline = headlines.get(variant, headlines["ready"])

    bodies = {
        "ready": (f"Recording {recording_reference} for {event_title} finished validation "
                  f"at {finalized_at}."),
        "partial": (f"Recording {recording_reference} for {event_title} validated at "
                    f"{finalized_at} with some media missing."),
        "failed": (f"Recording {recording_reference} for {event_title} could not be "
                   f"finalized at {finalized_at}."),
        "recovered": (f"Recording {recording_reference} for {event_title} became valid at "
                      f"{finalized_at} after a previous failure."),
    }
    body_txt = bodies.get(variant, bodies["ready"])

    rows = [("Recording", recording_reference), ("Event", event_title),
            ("Organization", org_name), ("Finalized", finalized_at),
            ("Asset status", asset_status), ("Duration", duration)]
    if variant == "recovered" and recovered_components:
        rows.append(("Recovered components", recovered_components))
    rows.append(("Checks that ran", validated_components))
    if missing_components:
        rows.append(("Missing or failed components", missing_components))
    if failure_category:
        rows.append(("Failure category", failure_category))
    if remediation:
        rows.append(("Recovery options", remediation))
    rows.append(("Checks NOT performed", not_checked))
    rows.append(("Replay availability", replay_status))

    url = recordings_url()
    return _send(
        to, media_subject(subject, test_mode),
        _org_shell(titles.get(variant, "Validation"), headline, MED_008_PREHEADER, rows,
                   html.escape(body_txt), MED_008_CTA, url, MED_008_REPLAY_SEPARATION),
        _org_text(headline, MED_008_PREHEADER, rows, body_txt, MED_008_CTA, url,
                  MED_008_REPLAY_SEPARATION, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


# == MED-009 Replay lifecycle ============================================================
# Recipients are the ASSET OWNER and authorized PUBLISHERS — users holding the `media_access`
# commercial permission (security.commercial_can), which is the same permission that gates
# the publish route itself. A purchaser is never substituted for a publisher: the audience
# communication for a replay is a different family with a different audience, and
# services/replay_comms.py cannot reach it.
#
# Fields this platform does not have are ABSENT, not invented. There are no geography
# restrictions, no viewer concurrency limits, and no captions or language tracks anywhere in
# this codebase, so the disclosure rows say so explicitly instead of rendering a blank that
# reads as "none configured".

MED_009_PREPARED_SUBJECT = "Zoiko Steam replay is prepared for review"
MED_009_PUBLISHED_SUBJECT = "Zoiko Steam replay was published"
MED_009_ACCESS_SUBJECT = "Zoiko Steam replay access changed"
MED_009_WITHDRAWN_SUBJECT = "Zoiko Steam replay was withdrawn"
MED_009_EXPIRED_SUBJECT = "Zoiko Steam replay availability expired"
MED_009_PREHEADER = "Review the replay and its access configuration."
MED_009_CTA = "Review replay"

MED_009_NOT_PUBLIC = (
    "This replay is not publicly available. Publishing is a separate, deliberate action."
)

# Stated verbatim on every MED-009 message that discloses access configuration.
MED_009_UNSUPPORTED = (
    "Captions, translated language tracks, geographic restrictions and viewer concurrency "
    "limits are not supported by Zoiko Steam and are therefore not configured on any replay."
)


def replay_url():
    return f"{public_base_url()}/organization/events"


def send_replay_prepared_email(to, *, event_title, asset_reference, source_recording,
                               prepared_at, intended_access, validation_state, org_name,
                               test_mode=False):
    """MED-009 Prepared. Explicitly does NOT imply availability — the replay has reached
    ready-for-review, which is the state before anyone decided to publish it."""
    body_txt = (
        f"A replay for {event_title} was prepared for review at {prepared_at}."
    )
    rows = [("Replay", asset_reference), ("Event", event_title), ("Organization", org_name),
            ("Source recording", source_recording), ("Prepared", prepared_at),
            ("Current state", "Prepared — awaiting review"),
            ("Source validation", validation_state),
            ("Intended access", intended_access),
            ("Captions", "Not supported"), ("Language tracks", "Not supported")]
    url = replay_url()
    footer = f"{MED_009_NOT_PUBLIC} {MED_009_UNSUPPORTED}"
    return _send(
        to, media_subject(MED_009_PREPARED_SUBJECT, test_mode),
        _org_shell("Replay prepared", "A replay is prepared for review.", MED_009_PREHEADER,
                   rows, html.escape(body_txt), MED_009_CTA, url, footer),
        _org_text("A replay is prepared for review.", MED_009_PREHEADER, rows, body_txt,
                  MED_009_CTA, url, footer, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


def send_replay_published_email(to, *, event_title, asset_reference, published_at, audience,
                                availability_window, download_permission, delivery_state,
                                org_name, test_mode=False):
    """MED-009 Published.

    `delivery_state` matters: publish_replay queues a watermark burn and the audience URL
    stays withheld until it finishes, so "published" and "watchable right now" are not the
    same instant. Reporting the publish without that distinction would be the same class of
    error as calling a stopped recording a ready replay.
    """
    body_txt = f"The replay for {event_title} was published at {published_at}."
    rows = [("Replay", asset_reference), ("Event", event_title), ("Organization", org_name),
            ("Published", published_at), ("Audience", audience),
            ("Availability window", availability_window),
            ("Download permitted", download_permission),
            ("Delivery state", delivery_state),
            ("Geographic restrictions", "Not supported"),
            ("Viewer concurrency limit", "Not supported"),
            ("Captions", "Not supported"), ("Language tracks", "Not supported")]
    url = replay_url()
    return _send(
        to, media_subject(MED_009_PUBLISHED_SUBJECT, test_mode),
        _org_shell("Replay published", "A replay was published.", MED_009_PREHEADER, rows,
                   html.escape(body_txt), MED_009_CTA, url, MED_009_UNSUPPORTED),
        _org_text("A replay was published.", MED_009_PREHEADER, rows, body_txt,
                  MED_009_CTA, url, MED_009_UNSUPPORTED, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


def send_replay_access_changed_email(to, *, event_title, asset_reference, previous_access,
                                     current_access, effective_at, availability_window,
                                     org_name, test_mode=False):
    body_txt = (
        f"Replay access for {event_title} changed from {previous_access} to "
        f"{current_access} at {effective_at}."
    )
    rows = [("Replay", asset_reference), ("Event", event_title), ("Organization", org_name),
            ("Previous access", previous_access), ("Current access", current_access),
            ("Effective", effective_at), ("Availability window", availability_window),
            ("Geographic restrictions", "Not supported"),
            ("Viewer concurrency limit", "Not supported")]
    url = replay_url()
    return _send(
        to, media_subject(MED_009_ACCESS_SUBJECT, test_mode),
        _org_shell("Replay access changed", "Replay access changed.", MED_009_PREHEADER,
                   rows, html.escape(body_txt), MED_009_CTA, url, MED_009_UNSUPPORTED),
        _org_text("Replay access changed.", MED_009_PREHEADER, rows, body_txt, MED_009_CTA,
                  url, MED_009_UNSUPPORTED, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


def send_replay_withdrawn_email(to, *, event_title, asset_reference, effective_at, owner,
                                reason, entitlement_effect, org_name, test_mode=False):
    """`entitlement_effect` is the one field a withdrawal notice can get badly wrong.

    Zoiko Steam has no refund or entitlement-reversal behaviour attached to withdrawal, so
    the caller passes what the platform actually does — stops serving the replay — and this
    template never speculates about refunds.
    """
    body_txt = f"The replay for {event_title} was withdrawn at {effective_at}."
    rows = [("Replay", asset_reference), ("Event", event_title), ("Organization", org_name),
            ("Withdrawn", effective_at), ("Asset owner", owner), ("Reason", reason),
            ("Effect on existing access", entitlement_effect)]
    url = replay_url()
    footer = "The recording itself is unchanged. Withdrawal removes replay access only."
    return _send(
        to, media_subject(MED_009_WITHDRAWN_SUBJECT, test_mode),
        _org_shell("Replay withdrawn", "A replay was withdrawn.", MED_009_PREHEADER, rows,
                   html.escape(body_txt), MED_009_CTA, url, footer),
        _org_text("A replay was withdrawn.", MED_009_PREHEADER, rows, body_txt, MED_009_CTA,
                  url, footer, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


def send_replay_expired_email(to, *, event_title, asset_reference, expired_at, owner,
                              org_name, test_mode=False):
    """Only sent because replay expiry is now ENFORCED at the access layer
    (routers/events.py::watch_event). Before that it was a stored date nothing honoured,
    and announcing expiry while the replay stayed watchable would have been false."""
    body_txt = f"Replay availability for {event_title} expired at {expired_at}."
    rows = [("Replay", asset_reference), ("Event", event_title), ("Organization", org_name),
            ("Expired", expired_at), ("Asset owner", owner),
            ("Current access", "Expired — viewers can no longer watch this replay")]
    url = replay_url()
    footer = ("The recording is retained under its retention policy. Republishing is a "
              "separate operator action.")
    return _send(
        to, media_subject(MED_009_EXPIRED_SUBJECT, test_mode),
        _org_shell("Replay expired", "Replay availability expired.", MED_009_PREHEADER,
                   rows, html.escape(body_txt), MED_009_CTA, url, footer),
        _org_text("Replay availability expired.", MED_009_PREHEADER, rows, body_txt,
                  MED_009_CTA, url, footer, "Zoiko Steam Media Operations"),
        sender=_sender_identity(SENDER_MEDIA),
    )


# == MED-011 Retention, legal hold and deletion ==========================================
# Recipients: asset owner, data-governance contacts, and (for hold messages) legal-hold
# contacts — a real stored list (models.media_governance.LegalHoldContact), because the
# person who asked for a hold is frequently not a Zoiko Steam user at all.
#
# No template here carries a legal instruction, counsel's reasoning, or a cloud storage
# path. Holds are described by a closed category plus an opaque reference.

SENDER_GOVERNANCE = "Zoiko Steam Data Governance"

MED_011_WARNING_SUBJECT = "Action required: Zoiko Steam recording retention period is ending"
MED_011_HOLD_SUBJECT = "Zoiko Steam recording is under legal hold"
MED_011_HOLD_RELEASED_SUBJECT = "Zoiko Steam legal hold was released"
MED_011_EXTENSION_REQUESTED_SUBJECT = "Approval requested: Zoiko Steam retention extension"
MED_011_EXTENSION_DECIDED_SUBJECT = "Zoiko Steam retention extension was {decision}"
MED_011_DELETED_SUBJECT = "Zoiko Steam recording deletion completed"
MED_011_DELETE_FAILED_SUBJECT = "Action required: Zoiko Steam recording deletion failed"
MED_011_PREHEADER = "Review this asset's retention state."
MED_011_CTA = "Review recording"


def send_retention_warning_email(to, *, event_title, asset_reference, retention_until,
                                 policy_version, owner, days_remaining, deletion_behaviour,
                                 org_name, test_mode=False):
    """`deletion_behaviour` states what actually happens at the deadline.

    There is no automatic deletion scheduler in this platform. Saying "this recording will
    be deleted on <date>" would therefore be false, so the caller passes the true behaviour:
    the asset becomes ELIGIBLE for deletion and an operator must act.
    """
    body_txt = (
        f"The retention period for the recording of {event_title} ends on {retention_until}."
    )
    rows = [("Recording", asset_reference), ("Event", event_title),
            ("Organization", org_name), ("Asset owner", owner),
            ("Retention ends", retention_until), ("Days remaining", str(days_remaining)),
            ("Retention policy", policy_version),
            ("What happens at the deadline", deletion_behaviour)]
    url = recordings_url()
    footer = ("To keep this recording longer, request a retention extension. Extensions are "
              "granted by Zoiko Steam platform governance, not by the Organization.")
    return _send(
        to, MED_011_WARNING_SUBJECT,
        _org_shell("Retention ending", "A recording's retention period is ending.",
                   MED_011_PREHEADER, rows, html.escape(body_txt), MED_011_CTA, url, footer),
        _org_text("A recording's retention period is ending.", MED_011_PREHEADER, rows,
                  body_txt, MED_011_CTA, url, footer, "Zoiko Steam Data Governance"),
        sender=_sender_identity(SENDER_GOVERNANCE),
    )


def send_legal_hold_email(to, *, event_title, asset_reference, placed_at, hold_reference,
                          hold_category, owner, org_name, released=False, released_at=None,
                          test_mode=False):
    """Hold placed / released. Carries a CATEGORY and an opaque REFERENCE only.

    A legal hold's actual subject matter is frequently privileged and is never stored on the
    recording, so there is no parameter here that could carry it.
    """
    subject = MED_011_HOLD_RELEASED_SUBJECT if released else MED_011_HOLD_SUBJECT
    headline = ("A legal hold was released." if released
                else "A recording is under legal hold.")
    body_txt = (
        (f"The legal hold on the recording of {event_title} was released at {released_at}.")
        if released else
        (f"The recording of {event_title} was placed under legal hold at {placed_at}.")
    )
    rows = [("Recording", asset_reference), ("Event", event_title),
            ("Organization", org_name), ("Asset owner", owner),
            ("Hold reference", hold_reference), ("Hold category", hold_category)]
    rows.append(("Released", released_at) if released else ("Placed", placed_at))
    rows.append(("Deletion", "Permitted again, subject to the retention policy" if released
                 else "Blocked — this recording cannot be deleted while the hold is open"))
    url = recordings_url()
    footer = ("Legal holds are placed and released by Zoiko Steam platform governance. "
              "Contact platform support with the hold reference for any question about it.")
    return _send(
        to, subject,
        _org_shell("Legal hold", headline, MED_011_PREHEADER, rows, html.escape(body_txt),
                   MED_011_CTA, url, footer),
        _org_text(headline, MED_011_PREHEADER, rows, body_txt, MED_011_CTA, url, footer,
                  "Zoiko Steam Data Governance"),
        sender=_sender_identity(SENDER_GOVERNANCE),
    )


def send_retention_extension_requested_email(to, *, event_title, asset_reference, requester,
                                             reason_category, current_until, requested_until,
                                             org_name, test_mode=False):
    body_txt = (
        f"A retention extension was requested for the recording of {event_title}."
    )
    rows = [("Recording", asset_reference), ("Event", event_title),
            ("Organization", org_name), ("Requested by", requester),
            ("Reason category", reason_category), ("Current retention", current_until),
            ("Requested retention", requested_until),
            ("Status", "Pending platform governance approval")]
    url = recordings_url()
    footer = ("A retention extension takes effect only when it is approved. Until then the "
              "existing retention date stands.")
    return _send(
        to, MED_011_EXTENSION_REQUESTED_SUBJECT,
        _org_shell("Retention extension requested", "A retention extension was requested.",
                   MED_011_PREHEADER, rows, html.escape(body_txt), MED_011_CTA, url, footer),
        _org_text("A retention extension was requested.", MED_011_PREHEADER, rows, body_txt,
                  MED_011_CTA, url, footer, "Zoiko Steam Data Governance"),
        sender=_sender_identity(SENDER_GOVERNANCE),
    )


def send_retention_extension_decided_email(to, *, event_title, asset_reference, decision,
                                           requester, approver, reason_category,
                                           previous_until, new_until, effective_at,
                                           org_name, test_mode=False):
    """`decision` is "approved" or "declined". A declined extension explicitly restates the
    unchanged retention date, so nobody reads a decision notice as a reprieve."""
    approved = decision == "approved"
    headline = (f"A retention extension was {decision}.")
    body_txt = (
        f"The retention extension for the recording of {event_title} was {decision} at "
        f"{effective_at}."
    )
    rows = [("Recording", asset_reference), ("Event", event_title),
            ("Organization", org_name), ("Decision", decision.title()),
            ("Requested by", requester), ("Decided by", approver),
            ("Reason category", reason_category),
            ("Previous retention", previous_until),
            ("Current retention", new_until if approved else previous_until),
            ("Effective", effective_at)]
    url = recordings_url()
    footer = ("The retention date shown above is the committed one." if approved else
              "The retention date is unchanged. The recording remains eligible for "
              "deletion on the date shown.")
    return _send(
        to, MED_011_EXTENSION_DECIDED_SUBJECT.format(decision=decision),
        _org_shell("Retention extension", headline, MED_011_PREHEADER, rows,
                   html.escape(body_txt), MED_011_CTA, url, footer),
        _org_text(headline, MED_011_PREHEADER, rows, body_txt, MED_011_CTA, url, footer,
                  "Zoiko Steam Data Governance"),
        sender=_sender_identity(SENDER_GOVERNANCE),
    )


def send_recording_deleted_email(to, *, event_title, asset_reference, deleted_at, requester,
                                 primary_removed, derived_removed, residual_copies,
                                 retained_metadata, org_name, test_mode=False):
    """Sent only after deletion actually succeeded.

    The four disclosure rows exist because "deleted" means different things to different
    readers. This platform can only truthfully speak for the objects it manages; it cannot
    speak for its cloud provider's own backup retention, so `residual_copies` says that
    rather than claiming permanent erasure.
    """
    body_txt = (
        f"The recording of {event_title} was deleted at {deleted_at}."
    )
    rows = [("Recording", asset_reference), ("Event", event_title),
            ("Organization", org_name), ("Deleted", deleted_at),
            ("Deleted by", requester),
            ("Primary media removed", primary_removed),
            ("Derived copies removed", derived_removed),
            ("Residual copies", residual_copies),
            ("Retained records", retained_metadata)]
    url = recordings_url()
    footer = ("Zoiko Steam does not claim that every copy everywhere has been erased. The "
              "rows above describe exactly what was removed.")
    return _send(
        to, MED_011_DELETED_SUBJECT,
        _org_shell("Deletion completed", "A recording was deleted.", MED_011_PREHEADER,
                   rows, html.escape(body_txt), MED_011_CTA, url, footer),
        _org_text("A recording was deleted.", MED_011_PREHEADER, rows, body_txt,
                  MED_011_CTA, url, footer, "Zoiko Steam Data Governance"),
        sender=_sender_identity(SENDER_GOVERNANCE),
    )


def send_recording_deletion_failed_email(to, *, event_title, asset_reference, failed_at,
                                         failure_category, current_state, remediation,
                                         org_name, test_mode=False):
    """`failure_category` is a closed-set category, never the provider's exception text —
    cloud storage errors routinely embed bucket names, object paths and request signatures."""
    body_txt = (
        f"Deletion of the recording of {event_title} did not complete at {failed_at}."
    )
    rows = [("Recording", asset_reference), ("Event", event_title),
            ("Organization", org_name), ("Failed", failed_at),
            ("Failure category", failure_category),
            ("Current retention and access state", current_state),
            ("Remediation", remediation)]
    url = recordings_url()
    footer = ("The recording still exists and is still subject to its retention policy. "
              "Nothing was partially removed.")
    return _send(
        to, MED_011_DELETE_FAILED_SUBJECT,
        _org_shell("Deletion failed", "A recording deletion failed.", MED_011_PREHEADER,
                   rows, html.escape(body_txt), MED_011_CTA, url, footer),
        _org_text("A recording deletion failed.", MED_011_PREHEADER, rows, body_txt,
                  MED_011_CTA, url, footer, "Zoiko Steam Data Governance"),
        sender=_sender_identity(SENDER_GOVERNANCE),
    )


# == ORG-012 Organization notification preferences ========================================
# Class C (Transactional) - Sender: Zoiko Steam - Recipients: preference owner, plus the
# Organization owner when organization-wide routing changed.
#
# The mandatory-communications sentence is load-bearing and is not decoration: this is the
# one message whose reader has just been told they control what Zoiko Steam sends them, so
# it is exactly where the limits of that control have to be stated.
#
# The body renders labelled On/Off transitions, never the stored JSON. A settings page that
# hands a reader `{"member_joined": false}` has told them nothing they can act on.

ORG_012_SUBJECT = "Your Zoiko Steam notification preferences changed"
ORG_012_PREHEADER = "Review the current recipients, thresholds, and channels."
ORG_012_HEADLINE = "Notification settings were updated."
ORG_012_CTA = "Review notification preferences"
ORG_012_MANDATORY_NOTE = (
    "Mandatory security, legal, access, and contract communications cannot be disabled."
)


def notification_preferences_url() -> str:
    return f"{public_base_url()}/organization/settings?tab=notifications"


def send_notification_preferences_email(to, *, change_summary, effective_at, scope,
                                        actor=None):
    """ORG-012. `change_summary` is a list of rendered 'Label: On -> Off' lines."""
    summary_text = "; ".join(change_summary) if change_summary else "Preferences updated"
    body_txt = (
        f"{summary_text} took effect at {effective_at} for {scope}. "
        f"{ORG_012_MANDATORY_NOTE}"
    )
    rows = [("Scope", scope), ("Effective", effective_at)]
    if actor:
        rows.append(("Changed by", actor))
    rows += [(line.split(":", 1)[0], line.split(":", 1)[1].strip())
             for line in change_summary]
    url = notification_preferences_url()
    return _send(
        to,
        ORG_012_SUBJECT,
        _org_shell("Notification preferences", ORG_012_HEADLINE, ORG_012_PREHEADER, rows,
                   html.escape(body_txt), ORG_012_CTA, url, ORG_012_MANDATORY_NOTE),
        _org_text(ORG_012_HEADLINE, ORG_012_PREHEADER, rows, body_txt, ORG_012_CTA, url,
                  ORG_012_MANDATORY_NOTE, "Zoiko Steam"),
        sender=_sender_identity(SENDER_DEFAULT),
    )


# == ORG-003 Role, workspace and mode access changed =====================================
# Class A (Security) - Sender: Zoiko Steam Security - Recipients: affected member +
# relevant administrators. Mandatory: no notification preference is consulted.
#
# The canonical control list requires step-up authentication and possibly dual approval for
# high-risk grants. Neither exists in this codebase - there is no recent-auth check, no
# approval workflow and no privileged-grant model. The notification is therefore sent for
# real committed changes only, and the copy never claims either control was applied.

ORG_003_SUBJECT = "Your Zoiko Steam access changed"
ORG_003_PREHEADER = "Review your current Organization and workspace permissions."
ORG_003_HEADLINE = "Your access permissions were updated."
ORG_003_CTA = "Review current access"
ORG_003_FOOTER = (
    "If you did not expect this change, contact an authorized administrator of this "
    "Organization immediately."
)


def access_url() -> str:
    return f"{public_base_url()}/organization/settings"


def send_access_changed_email(to, *, org_name, effective_at, previous_access,
                              current_access, is_affected_member=True):
    """ORG-003. previous_access / current_access are rendered role/scope strings."""
    if is_affected_member:
        body_txt = (
            f"An authorized administrator changed your access to {org_name} effective "
            f"{effective_at}. Previous: {previous_access}. Current: {current_access}."
        )
    else:
        # Administrator copy. Same facts, correct grammar - an admin is not the subject of
        # the change and must not be told "your access" changed.
        body_txt = (
            f"An authorized administrator changed a member's access to {org_name} effective "
            f"{effective_at}. Previous: {previous_access}. Current: {current_access}."
        )
    rows = [("Organization", org_name), ("Effective", effective_at),
            ("Previous", previous_access), ("Current", current_access)]
    url = access_url()
    return _send(
        to,
        ORG_003_SUBJECT,
        _org_shell("Access changed", ORG_003_HEADLINE, ORG_003_PREHEADER, rows,
                   html.escape(body_txt), ORG_003_CTA, url, ORG_003_FOOTER),
        _org_text(ORG_003_HEADLINE, ORG_003_PREHEADER, rows, body_txt, ORG_003_CTA, url,
                  ORG_003_FOOTER, "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


# == ORG-004 Membership removed ==========================================================
# Class A (Security) - Sender: Zoiko Steam Security - Recipients: removed member +
# relevant administrators.
#
# The identity-preservation line is mandatory and is what keeps ORG-004 distinct from
# IDN-008: this message says one Organization membership ended, NOT that the Zoiko identity
# was restricted or deleted. When the same operation also restricts the identity, IDN-008
# is sent separately and says so in its own words; ORG-004 never makes that claim.

ORG_004_SUBJECT = "Your access to {org} was removed"
ORG_004_PREHEADER = "You can no longer access this Organization in Zoiko Steam."
ORG_004_HEADLINE = "Your Organization membership ended."
ORG_004_CTA = "View your Organizations"
ORG_004_IDENTITY_PRESERVED = (
    "This does not delete your Zoiko identity or affect access to other Organizations."
)


def send_membership_removed_email(to, *, org_name, effective_at, is_affected_member=True):
    """ORG-004. No reason is disclosed: internal notes, HR and disciplinary detail and raw
    reason codes are all deliberately absent from the payload this function accepts."""
    if is_affected_member:
        body_txt = f"Your access was removed on {effective_at}. {ORG_004_IDENTITY_PRESERVED}"
        headline = ORG_004_HEADLINE
    else:
        body_txt = (
            f"A member's access to {org_name} was removed on {effective_at}. Their Zoiko "
            f"identity and any access to other Organizations are unaffected."
        )
        headline = "A member's Organization access ended."
    rows = [("Organization", org_name), ("Effective", effective_at), ("Status", "Removed")]
    url = organizations_url()
    return _send(
        to,
        ORG_004_SUBJECT.format(org=org_name),
        _org_shell("Membership ended", headline, ORG_004_PREHEADER, rows,
                   html.escape(body_txt), ORG_004_CTA, url, ORG_004_IDENTITY_PRESERVED),
        _org_text(headline, ORG_004_PREHEADER, rows, body_txt, ORG_004_CTA, url,
                  ORG_004_IDENTITY_PRESERVED, "Zoiko Steam Security"),
        sender=_sender_identity(SENDER_SECURITY),
    )


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
      <p>You can now invite hosts, add speakers, and publish the event
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


def _contributor_invite_html(name: str, event_title: str, org_name: str, backstage_url: str,
                              join_window_start: datetime | None, join_window_end: datetime | None,
                              consent_notice: str | None) -> str:
    safe_name = html.escape(name or "there")
    safe_title = html.escape(event_title or "an event")
    safe_org = html.escape(org_name or "your organization")
    window = (
        f"{join_window_start.strftime('%d %b %Y, %I:%M %p')} – {join_window_end.strftime('%I:%M %p')}"
        if join_window_start and join_window_end else
        f"Opens {join_window_start.strftime('%d %b %Y, %I:%M %p')}" if join_window_start else
        "Open now — no scheduled window"
    )
    notice = f'<p style="color:#888;font-size:13px;">{html.escape(consent_notice)}</p>' if consent_notice else ""
    return _shell(f"""
    {_header("You're invited to contribute")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>Hi {safe_name},</p>
      <p>{safe_org} has invited you to contribute to <strong>{safe_title}</strong> on ZoikoStream.
         Join the backstage to set up your camera and microphone before you go live.</p>
      <table style="width:100%;border-collapse:collapse;margin:24px 0;font-size:14px;">
        <tr><td style="padding:10px 0;color:#888;">Join window</td>
            <td style="padding:10px 0;text-align:right;">{html.escape(window)}</td></tr>
      </table>
      <p style="text-align:center;margin:32px 0;">
        <a href="{backstage_url}" style="background:#7ac142;color:#fff;text-decoration:none;
           padding:14px 28px;border-radius:4px;font-weight:bold;display:inline-block;">
          Open the backstage
        </a>
      </p>
      {notice}
      <p style="margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


def send_contributor_invite_email(to: str, name: str, event_title: str, org_name: str, backstage_url: str,
                                   join_window_start: datetime | None, join_window_end: datetime | None,
                                   consent_notice: str | None) -> None:
    _send(to, f"You're invited to contribute to {event_title}", _contributor_invite_html(
        name, event_title, org_name, backstage_url, join_window_start, join_window_end, consent_notice))


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
    # ZST-EC-001 COM-007. This previously appended "No charge was made." unconditionally, on
    # every failure, whatever the provider had actually done - so a customer whose card was
    # authorized and then failed at capture was told no charge existed while their bank showed
    # a hold. The strong claim now lives ONLY in services/commerce_comms, where
    # charge_position() derives it from committed Payment state. This legacy sender has no
    # access to that state, so it says the safe thing.
    lines.append("Please try again or contact us for help.")
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


def send_customer_export_email(to: str, name: str, event_title: str, export_url: str, expires_at) -> None:
    """BRD LE-AC-18 'controlled customer export' — services.delivery.create_export.
    Deliberately doesn't carry the file link itself, only a link to the token-gated page
    that generates a fresh, short-lived signed download URL on demand."""
    safe_title = html.escape(event_title or "your event")
    until = expires_at.strftime("%d %b %Y") if expires_at else None
    lines = [f"A validated recording for <strong>{safe_title}</strong> has been prepared for you."]
    if until:
        lines.append(f"This link is available until {until}, and only to you.")
    _send(to, f"Your recording is ready: {event_title}", _commercial_html(
        "Your recording is ready", name, lines, cta_label="Download recording", cta_url=export_url,
    ))


def send_event_report_email(to: str, name: str, event_title: str, report_url: str, expires_at) -> None:
    """BRD 'generated post-event audience and operations report' — services.report.release_report."""
    safe_title = html.escape(event_title or "your event")
    until = expires_at.strftime("%d %b %Y") if expires_at else None
    lines = [f"The event report for <strong>{safe_title}</strong> is ready to view."]
    if until:
        lines.append(f"This link is available until {until}, and only to you.")
    _send(to, f"Event report: {event_title}", _commercial_html(
        "Your event report", name, lines, cta_label="View report", cta_url=report_url,
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


def _contact_html(name: str, email: str, org: str, country: str, topic: str, message: str) -> str:
    """Internal inquiry notification. EVERY field is attacker-supplied, so every field is
    escaped — this email is read by our own staff, and an unescaped <script>/<img onerror>
    from a public form is a stored-XSS delivery vehicle aimed at us."""
    rows = [("Name", name), ("Work email", email), ("Organization", org or "—"),
            ("Country / region", country), ("Topic", topic)]
    row_html = "".join(
        f'<tr><td style="padding:8px 0;color:#888;white-space:nowrap;">{html.escape(k)}</td>'
        f'<td style="padding:8px 0;text-align:right;">{html.escape(v)}</td></tr>'
        for k, v in rows
    )
    # Newlines become <br> AFTER escaping, so the break markup cannot be smuggled in.
    safe_message = html.escape(message).replace("\n", "<br>")
    return _shell(f"""
    {_header("New contact enquiry")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <table style="width:100%;border-collapse:collapse;font-size:14px;">{row_html}</table>
      <div style="margin-top:20px;padding:16px;background:#f7f7f9;border-radius:6px;">
        {safe_message}
      </div>
      <p style="margin-bottom:0;color:#888;font-size:13px;">
        Sent from the ZoikoStream contact form. Reply directly to {html.escape(email)}.
      </p>
    </div>""")


def send_contact_message_email(*, first: str, last: str, email: str, org: str, country: str,
                                topic: str, message: str) -> None:
    """Deliver a public contact-form enquiry to the configured internal inbox.

    The recipient is settings.CONTACT_EMAIL and is NEVER derived from the request, so no
    payload can retarget an enquiry to an arbitrary address. The submitter's address appears
    only as escaped body text, never as a header.

    Subject is built from sanitized values: CR/LF are stripped because a newline inside a
    header is the classic header-injection primitive (it would let a submitter append their
    own Bcc:). Length is capped so a long name cannot push the real subject out of view.
    """
    name = f"{first} {last}".strip()
    # Strip anything that could terminate a header line, then bound the length.
    safe_subject_name = " ".join(name.replace("\r", " ").replace("\n", " ").split())[:80]
    safe_subject_topic = " ".join(topic.replace("\r", " ").replace("\n", " ").split())[:40]
    _send(
        settings.CONTACT_EMAIL,
        f"[{safe_subject_topic}] Enquiry from {safe_subject_name}",
        _contact_html(name, email, org, country, topic, message),
    )


# `send_welcome_email` was removed, not renamed. It fired at registration with copy that
# claimed an active account before verification; leaving the symbol in place would let a
# future caller reintroduce that defect. IDN-002 is `send_account_ready_email` above, and
# routers/auth.py::verify_email is its only trigger.


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

    # send_welcome_email/_welcome_html were removed when IDN-002 replaced the registration-
    # time "welcome" message with the verified-and-activated one; IDN-002's own coverage
    # lives in test_account_ready.py. The account-ready shell is checked here instead so
    # this block exercises something that still exists.
    with patch.object(settings, "RESEND_API_KEY", ""):
        send_reset_otp_email("nobody@example.com", "<script>", "0421")
    assert "0421" in _otp_html("Alice", "0421"), "otp not rendered"
    assert "Alice" in _otp_html("Alice", "0421")
    assert f"cid:{LOGO_CID}" in _otp_html("Alice", "0421"), "logo cid missing from otp email"
    assert _logo_attachment() and _logo_attachment()["content_id"] == LOGO_CID, "logo attachment missing"
    # ORG-001 replaced _invite_html. Same escaping guarantee, checked through the shell the
    # four invitation variants now share.
    invite = _org_shell("You're invited", "Join <b>Acme</b> on Zoiko Steam.",
                        ORG_001_PREHEADER, [("Role", "<i>Bob</i>")], "body",
                        ORG_001_CTA, "https://x/accept-invite?token=abc",
                        ORG_001_NOT_YET_MEMBER)
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


# == LVE-001 .. LVE-005 - live event operations ==========================================
# Customer-facing event and commercial communications. Every one of these reports committed
# proposal, intake, event, planning or rehearsal state.
#
# `[TEST MODE]` reuses media_subject() and therefore Organization.is_test - a real stored
# flag. Events carry no separate test/live mode of their own, which is reported as an
# unsupported requirement rather than inferred from a hostname or an event name.
#
# No template here accepts a contributor joining token, a LiveKit token, an access-link
# token or a stream key. There is no parameter that could carry one.

SENDER_EVENTS = "Zoiko Steam Event Operations"


def event_url(event_id: str) -> str:
    """Authenticated customer console for one event.

    No PII and no token in the URL: the event id is an opaque UUID, and the page behind it
    is behind the normal session check. Never an admin/Super Admin path.
    """
    return f"{public_base_url()}/organization/events/{event_id}"


def _lve_send(to, subject, header, headline, preheader, rows, body, cta, url, footer,
              test_mode):
    """One shell for every LVE message: HTML + plain text, safe CTA, named sender."""
    return _send(
        to, media_subject(subject, test_mode),
        _org_shell(header, headline, preheader, rows, html.escape(body), cta, url, footer),
        _org_text(headline, preheader, rows, body, cta, url, footer, SENDER_EVENTS),
        sender=_sender_identity(SENDER_EVENTS),
    )


def _bullets(items) -> str:
    return "; ".join(str(i) for i in items) if items else "None"


# -- LVE-001 Proposal and booking lifecycle ----------------------------------------------

LVE_001_READY_SUBJECT = "Your Zoiko Steam event proposal is ready"
LVE_001_ACCEPTED_SUBJECT = "Your Zoiko Steam event booking is confirmed"
LVE_001_CHANGED_SUBJECT = "Your Zoiko Steam event booking changed"
LVE_001_EXPIRED_SUBJECT = "Your Zoiko Steam event proposal expired"
LVE_001_PREHEADER = "Review the scope, responsibilities and validity period."


def send_proposal_ready_email(to, *, name, event_title, reference, scope, services,
                              customer_responsibilities, zoiko_responsibilities, assumptions,
                              amount, valid_until, validity_note, event_id, org_name,
                              test_mode=False):
    body = (f"Hi {name}, the proposal for {event_title} is ready for your review. It is not a "
            f"booking yet - the event is only booked once the proposal is accepted.")
    rows = [("Event", event_title), ("Proposal reference", reference),
            ("Organization", org_name), ("Approved scope", scope),
            ("Included services", _bullets(services)),
            ("Your responsibilities", _bullets(customer_responsibilities)),
            ("Zoiko Steam responsibilities", _bullets(zoiko_responsibilities)),
            ("Assumptions", _bullets(assumptions)),
            ("Amount", amount), ("Valid until", valid_until)]
    return _lve_send(to, LVE_001_READY_SUBJECT, "Proposal ready",
                     "Your event proposal is ready.", LVE_001_PREHEADER, rows, body,
                     "Review proposal", event_url(event_id), validity_note, test_mode)


def send_booking_accepted_email(to, *, name, event_title, reference, accepted_at, scope,
                                services, event_owner, commercial_contact, same_person,
                                next_step, event_id, org_name, test_mode=False):
    body = (f"Hi {name}, the booking for {event_title} was accepted at {accepted_at}. "
            f"The event is now confirmed.")
    contact_row = ("Commercial contact",
                   commercial_contact + (" (also the event owner)" if same_person else ""))
    rows = [("Event", event_title), ("Reference", reference), ("Organization", org_name),
            ("Accepted", accepted_at), ("Confirmed scope", scope),
            ("Included services", _bullets(services)),
            ("Event owner", event_owner), contact_row, ("Next step", next_step)]
    return _lve_send(to, LVE_001_ACCEPTED_SUBJECT, "Booking confirmed",
                     "Your event booking is confirmed.", LVE_001_PREHEADER, rows, body,
                     "Open event", event_url(event_id), next_step, test_mode)


def send_booking_changed_email(to, *, name, event_title, reference, previous_summary,
                               current_summary, effective_at, event_id, org_name,
                               test_mode=False):
    body = (f"Hi {name}, an approved change to the booking for {event_title} took effect at "
            f"{effective_at}.")
    rows = [("Event", event_title), ("Change reference", reference),
            ("Organization", org_name), ("Previous", previous_summary),
            ("Current", current_summary), ("Effective", effective_at)]
    return _lve_send(to, LVE_001_CHANGED_SUBJECT, "Booking changed",
                     "Your event booking changed.", LVE_001_PREHEADER, rows, body,
                     "Review booking", event_url(event_id),
                     "This change has already been approved and committed.", test_mode)


def send_proposal_expired_email(to, *, name, event_title, reference, expired_at,
                                reissue_note, event_id, org_name, test_mode=False):
    body = (f"Hi {name}, the proposal for {event_title} expired at {expired_at} and can no "
            f"longer be accepted.")
    rows = [("Event", event_title), ("Proposal reference", reference),
            ("Organization", org_name), ("Expired", expired_at)]
    return _lve_send(to, LVE_001_EXPIRED_SUBJECT, "Proposal expired",
                     "Your event proposal expired.", LVE_001_PREHEADER, rows, body,
                     "Open event", event_url(event_id), reissue_note, test_mode)


# -- LVE-002 Event intake lifecycle -------------------------------------------------------

LVE_002_OPENED_SUBJECT = "Action required: complete your Zoiko Steam event intake"
LVE_002_REMINDER_SUBJECT = "Reminder: your Zoiko Steam event intake is due"
LVE_002_INCOMPLETE_SUBJECT = "Action required: your Zoiko Steam event intake is incomplete"
LVE_002_COMPLETED_SUBJECT = "Your Zoiko Steam event intake is complete"
LVE_002_REOPENED_SUBJECT = "Action required: your Zoiko Steam event intake was reopened"
LVE_002_PREHEADER = "Complete the required sections in your event console."
LVE_002_CTA = "Complete intake"
LVE_002_FOOTER = ("Sign in to complete the intake. The link opens your event in the Zoiko "
                  "Steam console and carries no personal details.")


def send_intake_opened_email(to, *, name, event_title, opened_at, due_at, sections,
                             event_id, org_name, test_mode=False):
    body = (f"Hi {name}, the event intake for {event_title} is open. We need these details "
            f"before planning can begin.")
    rows = [("Event", event_title), ("Organization", org_name), ("Opened", opened_at),
            ("Due", due_at), ("Required sections", _bullets(sections))]
    return _lve_send(to, LVE_002_OPENED_SUBJECT, "Event intake",
                     "Please complete your event intake.", LVE_002_PREHEADER, rows, body,
                     LVE_002_CTA, event_url(event_id), LVE_002_FOOTER, test_mode)


def send_intake_reminder_email(to, *, name, event_title, due_at, sections, event_id,
                               org_name, test_mode=False):
    body = f"Hi {name}, the event intake for {event_title} is due at {due_at}."
    rows = [("Event", event_title), ("Organization", org_name), ("Due", due_at),
            ("Still needed", _bullets(sections))]
    return _lve_send(to, LVE_002_REMINDER_SUBJECT, "Intake reminder",
                     "Your event intake is due soon.", LVE_002_PREHEADER, rows, body,
                     LVE_002_CTA, event_url(event_id), LVE_002_FOOTER, test_mode)


def send_intake_incomplete_email(to, *, name, event_title, due_at, sections, event_id,
                                 org_name, test_mode=False):
    body = (f"Hi {name}, the event intake for {event_title} is missing required information.")
    rows = [("Event", event_title), ("Organization", org_name), ("Due", due_at),
            ("Missing", _bullets(sections))]
    return _lve_send(to, LVE_002_INCOMPLETE_SUBJECT, "Intake incomplete",
                     "Your event intake is incomplete.", LVE_002_PREHEADER, rows, body,
                     LVE_002_CTA, event_url(event_id), LVE_002_FOOTER, test_mode)


def send_intake_completed_email(to, *, name, event_title, completed_at, sections, event_id,
                                org_name, test_mode=False):
    body = (f"Hi {name}, the event intake for {event_title} was validated and completed at "
            f"{completed_at}.")
    rows = [("Event", event_title), ("Organization", org_name), ("Completed", completed_at),
            ("Validated sections", _bullets(sections))]
    return _lve_send(to, LVE_002_COMPLETED_SUBJECT, "Intake complete",
                     "Your event intake is complete.", LVE_002_PREHEADER, rows, body,
                     "Open event", event_url(event_id),
                     "Planning continues from here. We will contact you if anything else is "
                     "needed.", test_mode)


def send_intake_reopened_email(to, *, name, event_title, reopened_at, due_at, sections,
                               reason, event_id, org_name, test_mode=False):
    body = (f"Hi {name}, the event intake for {event_title} was reopened at {reopened_at} "
            f"because some details need updating.")
    rows = [("Event", event_title), ("Organization", org_name), ("Reopened", reopened_at),
            ("New due date", due_at), ("Sections to update", _bullets(sections)),
            ("Reason", reason)]
    return _lve_send(to, LVE_002_REOPENED_SUBJECT, "Intake reopened",
                     "Your event intake was reopened.", LVE_002_PREHEADER, rows, body,
                     LVE_002_CTA, event_url(event_id), LVE_002_FOOTER, test_mode)


# -- LVE-003 Event details and assigned team ----------------------------------------------

LVE_003_APPROVED_SUBJECT = "Your Zoiko Steam event is confirmed"
LVE_003_TEAM_ASSIGNED_SUBJECT = "Your Zoiko Steam event team has been assigned"
LVE_003_TEAM_CHANGED_SUBJECT = "Your Zoiko Steam event team changed"
LVE_003_PREHEADER = "Review the confirmed details for your event."


def send_event_approved_email(to, *, name, event_title, reference, start_at, end_at, zone,
                              local_note, delivery_model, access_model, contributor_plan,
                              recording_requirement, replay_policy, team, event_id, org_name,
                              test_mode=False):
    body = (f"Hi {name}, {event_title} is confirmed. These are the details we will deliver "
            f"against.")
    rows = [("Event", event_title), ("Event reference", reference),
            ("Organization", org_name), ("Starts", start_at), ("Ends", end_at),
            ("Event timezone", zone), ("Delivery", delivery_model),
            ("Audience access", access_model), ("Contributors", contributor_plan),
            ("Recording", recording_requirement), ("Replay", replay_policy),
            ("Assigned team", _bullets(team))]
    return _lve_send(to, LVE_003_APPROVED_SUBJECT, "Event confirmed",
                     "Your event is confirmed.", LVE_003_PREHEADER, rows, body,
                     "Open event", event_url(event_id), local_note, test_mode)


def send_event_team_email(to, *, name, variant, event_title, reference, previous_team,
                          current_team, effective_at, primary_contact, event_id, org_name,
                          test_mode=False):
    assigned = variant == "assigned"
    subject = (LVE_003_TEAM_ASSIGNED_SUBJECT if assigned
               else LVE_003_TEAM_CHANGED_SUBJECT)
    headline = ("Your event team has been assigned." if assigned
                else "Your event team changed.")
    body = (f"Hi {name}, the team for {event_title} "
            + ("has been assigned." if assigned else "changed.")
            + f" This took effect at {effective_at}.")
    rows = [("Event", event_title), ("Event reference", reference),
            ("Organization", org_name)]
    if not assigned:
        rows.append(("Previous", previous_team))
    rows += [("Current", current_team), ("Effective", effective_at),
             ("Primary operational contact", primary_contact)]
    return _lve_send(to, subject, "Event team", headline, LVE_003_PREHEADER, rows, body,
                     "Open event", event_url(event_id),
                     "Only the people assigned to your event are listed here.", test_mode)


# -- LVE-004 Planning actions required ----------------------------------------------------

LVE_004_SUBJECTS = {
    "contributors": "Action required: complete contributor planning for {event}",
    "audience_access": "Action required: complete audience-access planning for {event}",
    "accessibility": "Action required: complete accessibility planning for {event}",
    "recording_replay": "Action required: complete recording and replay planning for {event}",
}
LVE_004_PREHEADER = "One planning action is outstanding for your event."


def send_planning_action_email(to, *, name, category, category_label, event_title,
                               outstanding, assigned_owner, due_at, blocking, extra_note,
                               event_id, org_name, test_mode=False):
    subject = LVE_004_SUBJECTS[category].format(event=event_title)
    body = (f"Hi {name}, {category_label.lower()} planning for {event_title} is not complete "
            f"yet. You are recorded as the owner of this action.")
    rows = [("Event", event_title), ("Organization", org_name),
            ("Planning area", category_label),
            ("Outstanding", _bullets(outstanding)),
            ("Action owner", assigned_owner), ("Due", due_at),
            ("Blocking", "Yes - the event cannot proceed until this is resolved"
                         if blocking else "No")]
    footer = extra_note or ("Complete this in your event console. Only the owner of this "
                            "action receives this message.")
    return _lve_send(to, subject, "Planning action required",
                     f"{category_label} planning is outstanding.", LVE_004_PREHEADER, rows,
                     body, "Complete planning", event_url(event_id), footer, test_mode)


# -- LVE-005 Rehearsal lifecycle ----------------------------------------------------------

LVE_005_SCHEDULED_SUBJECT = "Zoiko Steam rehearsal scheduled for {event}"
LVE_005_REMINDER_SUBJECT = "Reminder: Zoiko Steam rehearsal for {event}"
LVE_005_COMPLETED_SUBJECT = "Zoiko Steam rehearsal completed for {event}"
LVE_005_REPEAT_SUBJECT = "Action required: another rehearsal is needed for {event}"
LVE_005_PREHEADER = "Rehearsal details for your event."
LVE_005_CTA = "Open event"


def send_rehearsal_scheduled_email(to, *, name, event_title, scheduled_at, zone, local_note,
                                   purpose, expected, joining, event_id, org_name,
                                   test_mode=False):
    body = (f"Hi {name}, a rehearsal for {event_title} is scheduled for {scheduled_at}.")
    rows = [("Event", event_title), ("Organization", org_name),
            ("Rehearsal", scheduled_at), ("Event timezone", zone), ("Purpose", purpose),
            ("Expected participants", _bullets(expected)), ("Joining", joining)]
    return _lve_send(to, LVE_005_SCHEDULED_SUBJECT.format(event=event_title), "Rehearsal",
                     "A rehearsal is scheduled.", LVE_005_PREHEADER, rows, body,
                     LVE_005_CTA, event_url(event_id), local_note, test_mode)


def send_rehearsal_reminder_email(to, *, name, event_title, scheduled_at, zone, local_note,
                                  joining, event_id, org_name, test_mode=False):
    body = f"Hi {name}, the rehearsal for {event_title} is coming up at {scheduled_at}."
    rows = [("Event", event_title), ("Organization", org_name),
            ("Rehearsal", scheduled_at), ("Event timezone", zone), ("Joining", joining)]
    return _lve_send(to, LVE_005_REMINDER_SUBJECT.format(event=event_title), "Rehearsal",
                     "Your rehearsal is coming up.", LVE_005_PREHEADER, rows, body,
                     LVE_005_CTA, event_url(event_id), local_note, test_mode)


def send_rehearsal_completed_email(to, *, name, event_title, scheduled_at, zone, local_note,
                                   completed_at, validated, outstanding, next_steps,
                                   event_id, org_name, test_mode=False):
    body = f"Hi {name}, the rehearsal for {event_title} completed at {completed_at}."
    rows = [("Event", event_title), ("Organization", org_name),
            ("Completed", completed_at), ("Event timezone", zone),
            ("Validated", _bullets(validated)),
            ("Outstanding issues", _bullets(outstanding)), ("Next steps", next_steps)]
    return _lve_send(to, LVE_005_COMPLETED_SUBJECT.format(event=event_title), "Rehearsal",
                     "The rehearsal is complete.", LVE_005_PREHEADER, rows, body,
                     LVE_005_CTA, event_url(event_id), local_note, test_mode)


def send_rehearsal_repeat_email(to, *, name, event_title, scheduled_at, zone, local_note,
                                reason, outstanding, next_action, next_at, joining,
                                event_id, org_name, test_mode=False):
    body = (f"Hi {name}, the rehearsal for {event_title} did not validate the setup, so "
            f"another rehearsal is needed.")
    rows = [("Event", event_title), ("Organization", org_name), ("Reason", reason),
            ("Unresolved", _bullets(outstanding)), ("Next action", next_action),
            ("Next rehearsal", next_at), ("Joining", joining)]
    return _lve_send(to, LVE_005_REPEAT_SUBJECT.format(event=event_title), "Rehearsal",
                     "Another rehearsal is needed.", LVE_005_PREHEADER, rows, body,
                     LVE_005_CTA, event_url(event_id), local_note, test_mode)


# == LVE-006 .. LVE-012 - readiness, event day, incidents, completion, evidence ==========
# Same shell, sender and [TEST MODE] rule as LVE-001..005. No template below accepts a
# LiveKit token, playback token, stream key, contributor credential, API key or admin URL -
# there is no parameter that could carry one, which is the structural guarantee.

# -- LVE-006 Readiness gate lifecycle -----------------------------------------------------

LVE_006_CONDITIONAL_SUBJECT = "Zoiko Steam event readiness is conditional"
LVE_006_BLOCKED_SUBJECT = "Action required: {event} is blocked from readiness"
LVE_006_PASSED_SUBJECT = "{event} passed Zoiko Steam readiness checks"
LVE_006_REGRESSED_SUBJECT = "Action required: {event} readiness changed"
LVE_006_PREHEADER = "Review the current readiness position for your event."


def send_readiness_email(to, *, name, variant, event_title, reference, current_state,
                         previous_state, regressed_at, conditions, exceptions, may_proceed,
                         impact, owners, event_id, org_name, test_mode=False):
    subject = {
        "conditional": LVE_006_CONDITIONAL_SUBJECT,
        "blocked": LVE_006_BLOCKED_SUBJECT.format(event=event_title),
        "passed": LVE_006_PASSED_SUBJECT.format(event=event_title),
        "regressed": LVE_006_REGRESSED_SUBJECT.format(event=event_title),
    }[variant]
    headline = {
        "conditional": "Readiness is conditional.",
        "blocked": "This event is blocked from readiness.",
        "passed": "This event passed readiness checks.",
        "regressed": "Readiness has changed and needs attention.",
    }[variant]
    body = {
        "conditional": (f"Hi {name}, {event_title} has cleared readiness only because an "
                        f"approved exception is carrying it."),
        "blocked": (f"Hi {name}, {event_title} cannot enter production until the conditions "
                    f"below are resolved."),
        "passed": f"Hi {name}, {event_title} has passed every required readiness check.",
        "regressed": (f"Hi {name}, {event_title} previously passed readiness and no longer "
                      f"does. This needs attention before the event."),
    }[variant]
    rows = [("Event", event_title), ("Event reference", reference),
            ("Organization", org_name), ("Current readiness", current_state)]
    if variant == "regressed":
        rows += [("Previous readiness", previous_state), ("Changed at", regressed_at)]
    rows += [("Outstanding conditions", _bullets(conditions)),
             # Whether the event may proceed is the readiness ENGINE's own answer, never an
             # independent judgement made in the message.
             ("May the event proceed?", "Yes" if may_proceed else "No"),
             ("Impact", impact),
             ("Responsible owners", _bullets(owners))]
    if exceptions:
        rows.append(("Approved exceptions", _bullets(exceptions)))
    footer = ("Conditions are shown as the readiness engine reported them. Internal risk and "
              "detection logic is not included.")
    return _lve_send(to, subject, "Event readiness", headline, LVE_006_PREHEADER, rows, body,
                     "Review readiness", event_url(event_id), footer, test_mode)


# -- LVE-007 Final event-day brief --------------------------------------------------------

LVE_007_SUBJECT = "Your Zoiko Steam event-day brief is ready"
LVE_007_PREHEADER = "The approved brief for your event day."


def send_event_brief_email(to, *, name, event_title, version, approved_at, starts, zone,
                           local_note, delivery, audience_access, contributors, recording,
                           replay, accessibility, team, escalation, event_id, org_name,
                           test_mode=False):
    body = (f"Hi {name}, version {version} of the event-day brief for {event_title} has been "
            f"approved. It is the operational plan we will deliver against.")
    rows = [("Event", event_title), ("Brief version", str(version)),
            ("Organization", org_name), ("Approved", approved_at), ("Starts", starts),
            ("Event timezone", zone), ("Delivery", delivery),
            ("Audience access", audience_access), ("Contributors", contributors),
            ("Recording", recording), ("Replay", replay),
            ("Accessibility", accessibility), ("Event team", _bullets(team)),
            ("Escalation contact", escalation)]
    return _lve_send(to, LVE_007_SUBJECT, "Event-day brief",
                     "Your event-day brief is approved.", LVE_007_PREHEADER, rows, body,
                     "View event-day brief", event_url(event_id), local_note, test_mode)


# -- LVE-008 Event schedule change --------------------------------------------------------

LVE_008_SUBJECT = "The schedule changed for {event}"
LVE_008_PREHEADER = "The date or time of your event has moved."


def send_schedule_change_email(to, *, name, audience, event_title, reference, previous_time,
                               new_time, previous_zone, new_zone, date_changed, local_note,
                               rehearsal_note, readiness_note, brief_note, contributor_note,
                               event_id, org_name, test_mode=False):
    """One template, three governed audiences. `audience` decides how much operational
    detail is appropriate - an attendee is told the new time, not the readiness position."""
    body = (f"Hi {name}, the schedule for {event_title} has changed. The new time is below.")
    rows = [("Event", event_title), ("Event reference", reference),
            ("Previous", f"{previous_time} ({previous_zone})"),
            ("New", f"{new_time} ({new_zone})"),
            ("Date changed", "Yes" if date_changed else "No - the time moved on the same day")]
    if audience == "team":
        rows += [("Rehearsal", rehearsal_note), ("Readiness", readiness_note),
                 ("Event-day brief", brief_note), ("Contributors", contributor_note)]
    elif audience == "contributor":
        rows += [("Rehearsal", rehearsal_note), ("Your access", contributor_note)]
    footer = (local_note if audience == "audience"
              else local_note + " Dependent plans have been re-evaluated where the platform "
                                "supports it.")
    return _lve_send(to, LVE_008_SUBJECT.format(event=event_title), "Schedule changed",
                     "The event schedule changed.", LVE_008_PREHEADER, rows, body,
                     "View event", event_url(event_id), footer, test_mode)


# -- LVE-009 Event-day activation ---------------------------------------------------------

LVE_009_ARMED_SUBJECT = "{event} is armed for live operation"
LVE_009_LIVE_SUBJECT = "{event} is live on Zoiko Steam"
LVE_009_PREHEADER = "Internal operations notice for your event team."


def send_activation_email(to, *, name, variant, event_title, reference, changed_at, start_at,
                          zone, local_note, event_id, org_name, test_mode=False):
    armed = variant == "armed"
    subject = (LVE_009_ARMED_SUBJECT if armed else LVE_009_LIVE_SUBJECT).format(
        event=event_title)
    body = (f"Hi {name}, {event_title} "
            + ("is armed and ready for live operation." if armed else "is now live.")
            + f" This happened at {changed_at}.")
    rows = [("Event", event_title), ("Event reference", reference),
            ("Organization", org_name),
            ("State", "Armed" if armed else "Live"), ("Confirmed at", changed_at),
            ("Scheduled start", start_at), ("Event timezone", zone)]
    footer = ("This is an internal operations notice for your event team. No audience "
              "communication was sent. " + local_note)
    return _lve_send(to, subject, "Event activation",
                     "Your event is armed." if armed else "Your event is live.",
                     LVE_009_PREHEADER, rows, body, "Open operations view",
                     event_url(event_id), footer, test_mode)


# -- LVE-010 Interruption and cancellation ------------------------------------------------

LVE_010_DELAYED_SUBJECT = "{event} is delayed"
LVE_010_HOLD_SUBJECT = "{event} is temporarily on hold"
LVE_010_RESUMED_SUBJECT = "{event} has resumed"
LVE_010_CANCELED_SUBJECT = "{event} was canceled"
LVE_010_PREHEADER = "An update on your event."


def send_event_incident_email(to, *, name, variant, event_title, reference, reason, summary,
                              next_update, current_state, started_at, resumed_at,
                              canceled_at, interruption_duration, event_id, org_name,
                              test_mode=False):
    subject = {
        "delayed": LVE_010_DELAYED_SUBJECT, "temporary_hold": LVE_010_HOLD_SUBJECT,
        "resumed": LVE_010_RESUMED_SUBJECT, "canceled": LVE_010_CANCELED_SUBJECT,
    }[variant].format(event=event_title)
    headline = {"delayed": "Your event is delayed.",
                "temporary_hold": "Your event is temporarily on hold.",
                "resumed": "Your event has resumed.",
                "canceled": "Your event was canceled."}[variant]
    body = {
        "delayed": f"Hi {name}, {event_title} is delayed. {summary}",
        "temporary_hold": f"Hi {name}, {event_title} is on hold. {summary}",
        "resumed": f"Hi {name}, {event_title} has resumed.",
        "canceled": f"Hi {name}, {event_title} was canceled. {summary}",
    }[variant]
    rows = [("Event", event_title), ("Event reference", reference),
            ("Organization", org_name), ("Reason category", reason),
            ("Current state", current_state)]
    if variant in ("delayed", "temporary_hold"):
        rows.append(("Started", started_at))
    if variant == "resumed":
        rows += [("Resumed", resumed_at), ("Interruption duration", interruption_duration)]
    if variant == "canceled":
        rows.append(("Canceled", canceled_at))
    # A next-update promise appears ONLY when an operator committed to a time. There is no
    # default and no "shortly" - an uncommitted promise is worse than none.
    if next_update:
        rows.append(("Next update", next_update))
    footer = ("We share a reason category rather than investigation detail. Your Zoiko Steam "
              "team can give you more once the review is complete.")
    return _lve_send(to, subject, "Event update", headline, LVE_010_PREHEADER, rows, body,
                     "View event", event_url(event_id), footer, test_mode)


# -- LVE-011 Completion and replay --------------------------------------------------------

LVE_011_ENDED_SUBJECT = "Your Zoiko Steam event has ended"
LVE_011_PROCESSING_SUBJECT = "Your Zoiko Steam replay is being prepared"
LVE_011_APPROVAL_SUBJECT = "Action required: review your Zoiko Steam replay"
LVE_011_PUBLISHED_SUBJECT = "Your Zoiko Steam replay is published"
LVE_011_UNAVAILABLE_SUBJECT = "Zoiko Steam replay is unavailable"
LVE_011_PREHEADER = "Where your recording and replay currently stand."


def send_event_ended_email(to, *, name, event_title, reference, ended_at, duration,
                           final_state, recording_status, replay_next, event_id, org_name,
                           test_mode=False):
    body = (f"Hi {name}, {event_title} ended at {ended_at} after {duration}.")
    rows = [("Event", event_title), ("Event reference", reference),
            ("Organization", org_name), ("Ended", ended_at), ("Duration", duration),
            ("Final state", final_state),
            # Reports where the recording ACTUALLY is. An event that just ended is still
            # uploading, so this must never read as "recording complete".
            ("Recording", recording_status), ("Replay", replay_next)]
    return _lve_send(to, LVE_011_ENDED_SUBJECT, "Event ended", "Your event has ended.",
                     LVE_011_PREHEADER, rows, body, "View event", event_url(event_id),
                     "Recording and replay are separate steps. We will tell you when each "
                     "one is done.", test_mode)


def send_replay_lifecycle_email(to, *, name, variant, event_title, reference, replay_status,
                                recording_status, reason, next_action, event_id, org_name,
                                test_mode=False):
    subject = {
        "replay_processing": LVE_011_PROCESSING_SUBJECT,
        "replay_approval_required": LVE_011_APPROVAL_SUBJECT,
        "replay_published": LVE_011_PUBLISHED_SUBJECT,
        "replay_unavailable": LVE_011_UNAVAILABLE_SUBJECT,
    }[variant]
    headline = {
        "replay_processing": "Your replay is being prepared.",
        "replay_approval_required": "Your replay is ready for review.",
        "replay_published": "Your replay is published.",
        "replay_unavailable": "Your replay is unavailable.",
    }[variant]
    cta = "Review replay" if variant == "replay_approval_required" else "View event"
    body = f"Hi {name}, here is the current position for the {event_title} replay."
    rows = [("Event", event_title), ("Event reference", reference),
            ("Organization", org_name), ("Replay", replay_status),
            ("Recording", recording_status), ("Next action", next_action)]
    if variant == "replay_unavailable":
        rows.append(("Reason", reason))
    return _lve_send(to, subject, "Replay", headline, LVE_011_PREHEADER, rows, body, cta,
                     event_url(event_id),
                     "A published replay is the only state your audience can watch.",
                     test_mode)


# -- LVE-012 Post-event evidence and retention --------------------------------------------

LVE_012_READY_SUBJECT = "Your Zoiko Steam post-event report is ready"
LVE_012_FAILED_SUBJECT = "Zoiko Steam could not generate your post-event report"
LVE_012_REVIEW_SUBJECT = "Zoiko Steam event incident review is ready"
LVE_012_CLOSED_SUBJECT = "Zoiko Steam event record is closed"
LVE_012_PREHEADER = "Post-event evidence for your event."


def send_post_event_report_email(to, *, name, variant, event_title, version, generated_at,
                                 window, metrics, privacy_note, failure_note, event_id,
                                 org_name, test_mode=False):
    ready = variant == "ready"
    subject = LVE_012_READY_SUBJECT if ready else LVE_012_FAILED_SUBJECT
    body = (f"Hi {name}, version {version} of the post-event report for {event_title} is "
            f"ready." if ready else
            f"Hi {name}, we could not generate the post-event report for {event_title}.")
    rows = [("Event", event_title), ("Organization", org_name),
            ("Report version", str(version)), ("Reporting window", window)]
    if ready:
        # Whole-event totals only. No segment breakdown: there is no approved disclosure
        # threshold in this platform, so granular audience figures are not emailed at all.
        rows += [("Generated", generated_at), ("Headline figures", _bullets(metrics))]
    footer = privacy_note if ready else failure_note
    return _lve_send(to, subject, "Post-event report",
                     "Your post-event report is ready." if ready
                     else "We could not generate your report.",
                     LVE_012_PREHEADER, rows, body,
                     "Open report" if ready else "View event", event_url(event_id), footer,
                     test_mode)


def send_incident_review_email(to, *, name, event_title, reference, occurred_at, category,
                               summary, event_id, org_name, test_mode=False):
    body = (f"Hi {name}, the incident review for {event_title} has been approved and is "
            f"available in your console.")
    rows = [("Event", event_title), ("Review reference", reference),
            ("Organization", org_name), ("Incident opened", occurred_at),
            ("Category", category), ("Summary", summary)]
    return _lve_send(to, LVE_012_REVIEW_SUBJECT, "Incident review",
                     "Your incident review is ready.", LVE_012_PREHEADER, rows, body,
                     "Open review", event_url(event_id),
                     "This is the approved customer-facing review. Internal investigation "
                     "notes are not included.", test_mode)


def send_record_closed_email(to, *, name, event_title, reference, closed_at, report_version,
                             recordings, legal_hold, retention_until, residual, event_id,
                             org_name, test_mode=False):
    body = (f"Hi {name}, the operational record for {event_title} is now closed.")
    rows = [("Event", event_title), ("Event reference", reference),
            ("Organization", org_name), ("Closed", closed_at),
            ("Final report version", str(report_version)),
            ("Recordings held", str(recordings)),
            ("Under legal hold", str(legal_hold)),
            ("Media retained until", retention_until)]
    # Never claims erasure: audit, security and billing records outlive the media by design.
    return _lve_send(to, LVE_012_CLOSED_SUBJECT, "Record closed",
                     "This event record is closed.", LVE_012_PREHEADER, rows, body,
                     "View event", event_url(event_id), residual, test_mode)


# == CON-001 .. CON-005 - event contributors =============================================
# Contributor-facing. Every CTA points at /contributor/events/{id} - a contributor-scoped
# surface, never an organizer console, never organization administration, never Super Admin.
#
# No template here accepts a LiveKit token, stream key, producer credential, API key or
# password: there is no parameter that could carry one. The only credential any of these
# carries is a contributor access token, which is exchanged server-side for a short-lived
# media credential (services/contributor_access.issue_media_credential).

SENDER_CONTRIBUTOR = "Zoiko Steam Event Team"


def contributor_url(event_id: str) -> str:
    """The contributor backstage surface for one event."""
    return f"{public_base_url()}/contributor/events/{event_id}"


def _con_send(to, subject, header, headline, preheader, rows, body, cta, url, footer,
              test_mode):
    return _send(
        to, media_subject(subject, test_mode),
        _org_shell(header, headline, preheader, rows, html.escape(body), cta, url, footer),
        _org_text(headline, preheader, rows, body, cta, url, footer, SENDER_CONTRIBUTOR),
        sender=_sender_identity(SENDER_CONTRIBUTOR),
    )


# -- CON-001 Contributor invitation lifecycle ---------------------------------------------

CON_001_INVITE_SUBJECT = "You're invited to contribute to {event} on Zoiko Steam"
CON_001_ACCEPTED_SUBJECT = "You're confirmed for {event}"
CON_001_REMINDER_SUBJECT = "Reminder: action needed before {event}"
CON_001_REVOKED_SUBJECT = "Your contributor access for {event} was revoked"
CON_001_EXPIRED_SUBJECT = "Your contributor invitation for {event} expired"
CON_001_PREHEADER = "Your contributor details for this event."


def send_contributor_invite_email(to, *, name, event_title, role, starts, zone, local_note,
                                  technical_check, technical_status, rehearsal, org_name,
                                  inviter, grants, consent, expires_at, accept_url,
                                  test_mode=False):
    body = (f"Hi {name}, {inviter} has invited you to take part in {event_title} as "
            f"{role}. Accepting confirms you can attend.")
    rows = [("Event", event_title), ("Your role", role), ("Organizer", org_name),
            ("Starts", starts), ("Event timezone", zone), ("This role lets you", grants),
            ("Technical check", technical_check), ("Rehearsal", rehearsal),
            ("Invitation expires", expires_at), ("Privacy", consent)]
    return _con_send(to, CON_001_INVITE_SUBJECT.format(event=event_title),
                     "Contributor invitation", "You have been invited to contribute.",
                     CON_001_PREHEADER, rows, body, "Accept invitation", accept_url,
                     local_note, test_mode)


def send_contributor_accepted_email(to, *, name, event_title, role, starts, zone, local_note,
                                    technical_check, technical_status, rehearsal, org_name,
                                    next_action, access_note, backstage, test_mode=False):
    body = f"Hi {name}, you are confirmed as {role} for {event_title}."
    rows = [("Event", event_title), ("Your role", role), ("Organizer", org_name),
            ("Starts", starts), ("Event timezone", zone),
            ("Technical check", technical_check), ("Rehearsal", rehearsal),
            ("Next step", next_action), ("Event-day access", access_note)]
    return _con_send(to, CON_001_ACCEPTED_SUBJECT.format(event=event_title),
                     "Contributor confirmed", "You are confirmed for this event.",
                     CON_001_PREHEADER, rows, body, "Open your backstage", backstage,
                     local_note, test_mode)


def send_contributor_reminder_email(to, *, name, event_title, role, starts, zone, local_note,
                                    technical_check, technical_status, rehearsal, org_name,
                                    outstanding, backstage, test_mode=False):
    body = (f"Hi {name}, there is still something to do before {event_title}.")
    rows = [("Event", event_title), ("Your role", role), ("Starts", starts),
            ("Event timezone", zone), ("Still to do", _bullets(outstanding)),
            ("Technical check", technical_status)]
    return _con_send(to, CON_001_REMINDER_SUBJECT.format(event=event_title),
                     "Contributor reminder", "One thing still needs your attention.",
                     CON_001_PREHEADER, rows, body, "Open your backstage", backstage,
                     local_note, test_mode)


def send_contributor_revoked_email(to, *, name, event_title, role, starts, zone, local_note,
                                   technical_check, technical_status, rehearsal, org_name,
                                   revoked_at, reason, test_mode=False):
    body = (f"Hi {name}, your contributor access for {event_title} has been withdrawn. Any "
            f"personal links you were sent no longer work.")
    rows = [("Event", event_title), ("Your role", role), ("Organizer", org_name),
            ("Withdrawn", revoked_at)]
    # Only a reason an operator explicitly recorded as customer-safe. Absent means the
    # message says nothing about why rather than inventing an explanation.
    if reason:
        rows.append(("Reason", reason))
    return _con_send(to, CON_001_REVOKED_SUBJECT.format(event=event_title),
                     "Contributor access withdrawn", "Your contributor access was withdrawn.",
                     CON_001_PREHEADER, rows, body, None, None,
                     "Contact the event organizer if you think this is a mistake.", test_mode)


def send_contributor_expired_email(to, *, name, event_title, role, starts, zone, local_note,
                                   technical_check, technical_status, rehearsal, org_name,
                                   expired_at, test_mode=False):
    body = (f"Hi {name}, your invitation to contribute to {event_title} expired at "
            f"{expired_at} and can no longer be accepted.")
    rows = [("Event", event_title), ("Your role", role), ("Organizer", org_name),
            ("Expired", expired_at)]
    return _con_send(to, CON_001_EXPIRED_SUBJECT.format(event=event_title),
                     "Invitation expired", "Your contributor invitation expired.",
                     CON_001_PREHEADER, rows, body, None, None,
                     "Ask the event organizer to send a new invitation if you still want to "
                     "take part.", test_mode)


# -- CON-002 Contributor technical check --------------------------------------------------

CON_002_REQUIRED_SUBJECT = "Action required: complete your technical check for {event}"
CON_002_PASSED_SUBJECT = "Your technical check passed for {event}"
CON_002_ATTENTION_SUBJECT = "Action required: technical check needs attention for {event}"
CON_002_EXPIRED_SUBJECT = "Please repeat your technical check for {event}"
CON_002_ALERT_SUBJECT = "A contributor technical check needs attention for {event}"
CON_002_PREHEADER = "Check your camera, microphone and browser before the event."


def send_contributor_tech_check_email(to, *, name, variant, event_title, role, starts, zone,
                                      local_note, technical_check, technical_status,
                                      rehearsal, org_name, deadline, completed_at,
                                      required_checks, tested, failures, network_note,
                                      validity, check_url, test_mode=False):
    subject = {
        "required": CON_002_REQUIRED_SUBJECT, "passed": CON_002_PASSED_SUBJECT,
        "needs_attention": CON_002_ATTENTION_SUBJECT, "expired": CON_002_EXPIRED_SUBJECT,
    }[variant].format(event=event_title)
    headline = {"required": "Please complete your technical check.",
                "passed": "Your technical check passed.",
                "needs_attention": "Your technical check needs attention.",
                "expired": "Please repeat your technical check."}[variant]
    body = {
        "required": (f"Hi {name}, before {event_title} we need to confirm your camera, "
                     f"microphone and browser will work."),
        "passed": f"Hi {name}, everything we need for {event_title} is working.",
        "needs_attention": (f"Hi {name}, we could not confirm everything for {event_title}. "
                            f"The details below show what to look at."),
        "expired": (f"Hi {name}, your earlier technical check for {event_title} is no longer "
                    f"current."),
    }[variant]
    rows = [("Event", event_title), ("Your role", role), ("Event starts", starts),
            ("Event timezone", zone)]
    if variant == "required":
        # Nothing is claimed to have passed yet.
        rows += [("Deadline", deadline), ("What we check", _bullets(required_checks))]
    elif variant == "passed":
        rows += [("Completed", completed_at), ("Confirmed", _bullets(tested)),
                 ("Valid for", validity)]
    elif variant == "needs_attention":
        # Safe categories only - no user agent, no device ids, no addresses, no raw
        # diagnostics of any kind.
        rows += [("Needs attention", _bullets(failures)), ("Deadline", deadline)]
    else:
        rows.append(("Deadline", deadline))
    footer = network_note if variant in ("required", "needs_attention") else local_note
    return _con_send(to, subject, "Technical check", headline, CON_002_PREHEADER, rows, body,
                     "Run technical check", check_url, footer, test_mode)


def send_contributor_tech_alert_email(to, *, name, contributor, role, event_title, failures,
                                      event_id, org_name, test_mode=False):
    """Operational alert to the ASSIGNED owner only - never the whole event team."""
    body = (f"Hi {name}, a contributor for {event_title} could not complete their technical "
            f"check.")
    rows = [("Event", event_title), ("Contributor", contributor), ("Role", role),
            ("Organization", org_name), ("Needs attention", _bullets(failures))]
    return _con_send(to, CON_002_ALERT_SUBJECT.format(event=event_title), "Technical check",
                     "A contributor needs help with their setup.", CON_002_PREHEADER, rows,
                     body, "Open event", contributor_url(event_id),
                     "Only the assigned event owner receives this alert.", test_mode)


# -- CON-003 Contributor rehearsal reminder -----------------------------------------------

CON_003_SUBJECT = "Reminder: rehearsal for {event}"
CON_003_PREHEADER = "Your rehearsal is coming up."


def send_contributor_rehearsal_email(to, *, name, event_title, role, starts, zone, local_note,
                                     technical_check, technical_status, rehearsal, org_name,
                                     rehearsal_at, purpose, preparation, backstage,
                                     test_mode=False):
    body = f"Hi {name}, your rehearsal for {event_title} is at {rehearsal_at}."
    rows = [("Event", event_title), ("Your role", role), ("Rehearsal", rehearsal_at),
            ("Event timezone", zone), ("Purpose", purpose),
            ("Technical check", technical_status), ("How to prepare", preparation)]
    return _con_send(to, CON_003_SUBJECT.format(event=event_title), "Rehearsal",
                     "Your rehearsal is coming up.", CON_003_PREHEADER, rows, body,
                     "Open your backstage", backstage, local_note, test_mode)


# -- CON-004 Contributor event-day access -------------------------------------------------

CON_004_SUBJECT = "Your access is ready for {event}"
CON_004_PREHEADER = "Your personal backstage link for this event."


def send_contributor_access_email(to, *, name, event_title, role, starts, zone, local_note,
                                  technical_check, technical_status, rehearsal, org_name,
                                  window_opens, join_url, forward_note, support,
                                  test_mode=False):
    """The link here is a contributor access token, NOT a media credential. It is exchanged
    server-side; the LiveKit token is minted only after authorization succeeds."""
    body = (f"Hi {name}, your backstage access for {event_title} is ready. Use the link "
            f"below when the join window opens.")
    rows = [("Event", event_title), ("Your role", role), ("Join window opens", window_opens),
            ("Event starts", starts), ("Event timezone", zone),
            ("Technical check", technical_status), ("Support", support)]
    return _con_send(to, CON_004_SUBJECT.format(event=event_title), "Backstage access",
                     "Your backstage access is ready.", CON_004_PREHEADER, rows, body,
                     "Open backstage", join_url,
                     # Generated from what the backend actually enforces for this grant.
                     forward_note + " " + local_note, test_mode)


# -- CON-005 Contributor session ended ----------------------------------------------------

CON_005_SUBJECT = "Your contributor session for {event} has ended"
CON_005_PREHEADER = "Your contributor session is closed."


def send_contributor_session_ended_email(to, *, name, event_title, role, starts, zone,
                                         local_note, technical_check, technical_status,
                                         rehearsal, org_name, ended_at, reason,
                                         further_action, test_mode=False):
    body = f"Hi {name}, your contributor session for {event_title} has ended."
    rows = [("Event", event_title), ("Your role", role), ("Organizer", org_name),
            ("Ended", ended_at), ("Reason", reason), ("Anything to do?", further_action)]
    return _con_send(to, CON_005_SUBJECT.format(event=event_title), "Session ended",
                     "Your contributor session has ended.", CON_005_PREHEADER, rows, body,
                     None, None,
                     "Thank you for taking part. Contributing to this event does not "
                     "subscribe you to any mailing list.", test_mode)


# == COM-006 / COM-007 / COM-008 - billing, payments, entitlements =======================
# Billing-facing. Every CTA is the customer billing surface, never Super Admin.
#
# Reference discipline, applied by services/commerce_comms:
#   invoice_number / order_number  -> customer-facing business references, shown in full
#   payment_reference / provider   -> processor identifiers, masked to the last 4
#   internal row ids               -> an 8-character stub, never the whole UUID
#
# No template here accepts a card number, CVV, processor token, API key or storage secret -
# there is no parameter through which one could travel.

SENDER_BILLING = "Zoiko Steam Billing"


def billing_date(moment) -> str:
    """A billing timestamp in UTC.

    Deliberately NOT the event timezone: an invoice belongs to a seller entity and an
    accounting period, not to an event's locale. UTC is stated so the value is unambiguous.
    """
    if moment is None:
        return "Not set"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return f"{moment.astimezone(timezone.utc).strftime('%d %b %Y, %I:%M %p')} UTC"


def billing_console_url() -> str:
    return f"{public_base_url()}/organization/billing"


def _com_send(to, subject, header, headline, preheader, rows, body, cta, url, footer):
    return _send(
        to, subject,
        _org_shell(header, headline, preheader, rows, html.escape(body), cta, url, footer),
        _org_text(headline, preheader, rows, body, cta, url, footer, SENDER_BILLING),
        sender=_sender_identity(SENDER_BILLING),
    )


# -- COM-006 Invoice and payment confirmation ---------------------------------------------

COM_006_INVOICE_SUBJECT = "Your Zoiko Steam invoice is available"
COM_006_RECEIVED_SUBJECT = "Payment received for your Zoiko Steam invoice"
COM_006_REFUND_SUBJECT = "A refund was issued for your Zoiko Steam account"
COM_006_CREDIT_SUBJECT = "A credit note was issued for your Zoiko Steam account"
COM_006_PREHEADER = "Billing details for your account."


def send_invoice_available_email(to, *, name, invoice_number, order_reference, event_title,
                                 issue_date, due_date, currency, amount_due, subtotal,
                                 tax_summary, billing_entity, org_name, billing_url):
    body = (f"Hi {name}, invoice {invoice_number} is available for {org_name}.")
    rows = [("Invoice number", invoice_number), ("Order reference", order_reference),
            ("Organization", org_name), ("For", event_title),
            ("Issue date", issue_date), ("Due date", due_date),
            ("Subtotal", subtotal), ("Tax", tax_summary),
            ("Amount due", amount_due), ("Billed by", billing_entity)]
    return _com_send(to, COM_006_INVOICE_SUBJECT, "Invoice available",
                     "Your invoice is available.", COM_006_PREHEADER, rows, body,
                     "View invoice", billing_url,
                     "Sign in to view or download the full invoice.")


def send_payment_received_email(to, *, name, amount, currency, paid_at, invoice_number,
                                payment_reference, method, balance, org_name, billing_url):
    body = f"Hi {name}, we have received your payment of {amount}. Thank you."
    rows = [("Amount received", amount), ("Received", paid_at),
            ("Invoice number", invoice_number), ("Organization", org_name),
            ("Payment method", method),
            # Masked - a processor reference, kept only so a bank line can be matched.
            ("Payment reference", payment_reference),
            ("Remaining balance", balance)]
    return _com_send(to, COM_006_RECEIVED_SUBJECT, "Payment received",
                     "Your payment has been received.", COM_006_PREHEADER, rows, body,
                     "View billing", billing_url,
                     "This payment is captured and settled.")


def send_refund_issued_email(to, *, name, amount, reference, issued_at, invoice_number,
                             reason, payment_reference, status, org_name, billing_url):
    """A REFUND: money is being returned. Distinct from a credit note."""
    body = (f"Hi {name}, a refund of {amount} has been issued for {org_name}. It will be "
            f"returned to your original payment method.")
    rows = [("Refund amount", amount), ("Refund reference", reference),
            ("Issued", issued_at), ("Related invoice", invoice_number),
            ("Organization", org_name), ("Reason", reason),
            ("Payment reference", payment_reference), ("Status", status)]
    return _com_send(to, COM_006_REFUND_SUBJECT, "Refund issued",
                     "A refund has been issued.", COM_006_PREHEADER, rows, body,
                     "View billing", billing_url,
                     "Your bank may take a few working days to show the refund.")


def send_credit_note_email(to, *, name, amount, reference, issued_at, invoice_number,
                           reason, effect, waiver, org_name, billing_url):
    """A CREDIT NOTE: an accounting adjustment, which may not return money at all.

    The distinction is stated in the body rather than left to inference, because a customer
    told "credit" who expects a bank refund will chase one that is never coming.
    """
    body = (f"Hi {name}, a credit note of {amount} has been applied to {org_name}.")
    rows = [("Credit amount", amount), ("Credit note reference", reference),
            ("Issued", issued_at), ("Related invoice", invoice_number),
            ("Organization", org_name),
            ("Type", "Fee waiver" if waiver else "Credit note"),
            ("Reason", reason), ("What this means", effect)]
    return _com_send(to, COM_006_CREDIT_SUBJECT, "Credit note",
                     "A credit note was issued.", COM_006_PREHEADER, rows, body,
                     "View billing", billing_url, effect)


# -- COM-007 Payment problem lifecycle ----------------------------------------------------

COM_007_FAILED_SUBJECT = "Action required: we could not complete your payment"
COM_007_OVERDUE_SUBJECT = "Action required: your Zoiko Steam invoice is overdue"
COM_007_RESOLVED_SUBJECT = "Your Zoiko Steam billing issue is resolved"
COM_007_PREHEADER = "Please review your billing details."


def send_payment_problem_email(to, *, name, variant, invoice_number, order_reference, amount,
                               currency, occurred_at, charge_note, category,
                               payment_reference, next_action, due_date, resolved_at,
                               org_name, billing_url):
    subject = {"failed": COM_007_FAILED_SUBJECT, "overdue": COM_007_OVERDUE_SUBJECT,
               "resolved": COM_007_RESOLVED_SUBJECT}[variant]
    headline = {"failed": "We could not complete your payment.",
                "overdue": "Your invoice is overdue.",
                "resolved": "Your billing issue is resolved."}[variant]
    if variant == "failed":
        # `charge_note` is DERIVED from committed payment state by
        # services/commerce_comms.charge_position. There is no unconditional
        # "no charge was made" sentence anywhere in this template.
        body = f"Hi {name}, a payment for {org_name} was not completed. {charge_note}"
    elif variant == "overdue":
        body = (f"Hi {name}, invoice {invoice_number} was due on {due_date} and is still "
                f"outstanding.")
    else:
        body = f"Hi {name}, the billing issue on your account is now resolved. {category}"

    rows = [("Invoice number", invoice_number), ("Order reference", order_reference),
            ("Organization", org_name), ("Amount", amount)]
    if variant == "failed":
        rows += [("Attempted", occurred_at), ("What happened", category),
                 ("Payment reference", payment_reference)]
    elif variant == "overdue":
        rows += [("Original due date", due_date), ("Amount outstanding", amount),
                 ("Current status", category)]
    else:
        rows += [("Resolved", resolved_at), ("Result", category)]
    rows.append(("Next step", next_action))
    footer = ("We share a general category rather than the processor's own decline detail."
              if variant == "failed" else
              "If you have already paid, it may take a short time to appear."
              if variant == "overdue" else
              "No further action is needed.")
    return _com_send(to, subject, "Billing", headline, COM_007_PREHEADER, rows, body,
                     "Open billing", billing_url, footer)


COM_007_DISPUTE_OPENED_SUBJECT = "A payment dispute was opened on your Zoiko Steam account"
COM_007_DISPUTE_ACTION_SUBJECT = "Action required: evidence needed for a payment dispute"
COM_007_DISPUTE_RESOLVED_SUBJECT = "A payment dispute on your Zoiko Steam account is resolved"


def send_dispute_email(to, *, name, variant, dispute_reference, provider_reference, amount,
                       opened_at, evidence_due, resolved_at, outcome, org_name, billing_url):
    """Dispute lifecycle. Raw evidence and fraud signals are never included."""
    subject = {"dispute_opened": COM_007_DISPUTE_OPENED_SUBJECT,
               "dispute_action_required": COM_007_DISPUTE_ACTION_SUBJECT,
               "dispute_resolved": COM_007_DISPUTE_RESOLVED_SUBJECT}[variant]
    headline = {"dispute_opened": "A payment dispute was opened.",
                "dispute_action_required": "A payment dispute needs evidence.",
                "dispute_resolved": "A payment dispute is resolved."}[variant]
    body = {
        "dispute_opened": (f"Hi {name}, a dispute was opened against a payment of {amount} "
                           f"on {org_name}. We are handling it with the payment provider."),
        "dispute_action_required": (f"Hi {name}, we need supporting information for a "
                                    f"dispute of {amount} on {org_name}."),
        "dispute_resolved": (f"Hi {name}, the dispute of {amount} on {org_name} has been "
                             f"resolved."),
    }[variant]
    rows = [("Dispute reference", dispute_reference), ("Organization", org_name),
            ("Amount", amount), ("Opened", opened_at),
            # Masked - identifies the case inside the processor.
            ("Provider reference", provider_reference)]
    if variant == "dispute_action_required" and evidence_due:
        rows.append(("Evidence needed by", evidence_due))
    if variant == "dispute_resolved":
        rows += [("Resolved", resolved_at), ("Outcome", outcome)]
    return _com_send(to, subject, "Payment dispute", headline, COM_007_PREHEADER, rows, body,
                     "Open billing", billing_url,
                     "We do not share the payment provider's investigation detail.")


# -- COM-008 Usage and entitlement lifecycle ----------------------------------------------

COM_008_LIMIT_SUBJECT = "Your Zoiko Steam {metric} limit has been reached"
COM_008_CHANGED_SUBJECT = "Your Zoiko Steam entitlements changed"
COM_008_REPORT_SUBJECT = "Your Zoiko Steam usage report is ready"
COM_008_CORRECTED_SUBJECT = "Your Zoiko Steam usage report was corrected"
COM_008_PREHEADER = "Usage and entitlements for your account."


def send_entitlement_limit_email(to, *, name, org_name, metric, used, limit, blocked_action,
                                 next_step, billing_url):
    body = (f"Hi {name}, {org_name} has reached its {metric.lower()} limit. {blocked_action}.")
    rows = [("Organization", org_name), ("Entitlement", metric),
            ("Current usage", used), ("Limit", limit),
            ("Blocked action", blocked_action), ("Next step", next_step)]
    return _com_send(to, COM_008_LIMIT_SUBJECT.format(metric=metric.lower()),
                     "Usage limit reached", f"Your {metric.lower()} limit is reached.",
                     COM_008_PREHEADER, rows, body, "View plan and usage", billing_url,
                     "You are told once each time this limit is reached, not on every "
                     "blocked attempt.")


def send_entitlement_changed_email(to, *, name, org_name, previous_plan, current_plan,
                                   changes, effective_at, billing_note, billing_url):
    body = f"Hi {name}, the entitlements for {org_name} have changed."
    rows = [("Organization", org_name), ("Previous plan", previous_plan),
            ("Current plan", current_plan), ("What changed", _bullets(changes)),
            ("Effective", effective_at)]
    # No price claim: a limit change is not evidence of a commercial price change.
    return _com_send(to, COM_008_CHANGED_SUBJECT, "Entitlements changed",
                     "Your entitlements changed.", COM_008_PREHEADER, rows, body,
                     "View plan and usage", billing_url, billing_note)


def send_usage_report_email(to, *, name, org_name, period, state, state_note, figures,
                            version, billing_url):
    """`state` is the STORED report state, so a provisional figure is always labelled."""
    body = (f"Hi {name}, the {state.lower()} usage report for {org_name} covering {period} "
            f"is ready.")
    rows = [("Organization", org_name), ("Reporting period", period),
            ("Report type", state), ("Version", str(version)),
            ("Figures", _bullets(figures))]
    return _com_send(to, COM_008_REPORT_SUBJECT, "Usage report",
                     f"Your {state.lower()} usage report is ready.", COM_008_PREHEADER,
                     rows, body, "Open usage report", billing_url, state_note)


def send_usage_corrected_email(to, *, name, org_name, period, changes, reason, corrected_at,
                               billing_url):
    body = (f"Hi {name}, the usage report for {org_name} covering {period} has been "
            f"corrected.")
    rows = [("Organization", org_name), ("Reporting period", period),
            ("Correction", _bullets(changes)), ("Reason", reason),
            ("Corrected", corrected_at)]
    return _com_send(to, COM_008_CORRECTED_SUBJECT, "Usage corrected",
                     "Your usage report was corrected.", COM_008_PREHEADER, rows, body,
                     "Open usage report", billing_url,
                     "The previous figures are shown above so the change is visible.")


# == SUP-001 .. SUP-004 - support cases ==================================================
# Customer-facing. Every CTA is /organization/support/{id} - the Tenant Console support
# view, never /admin and never a Super Admin path.
#
# Structural guarantees, enforced by the parameter lists below:
#   * NO template accepts `internal_notes`, an incident `detail`, a `commander`, a severity,
#     a password, a token, an API key or raw logs. There is no parameter one could travel in.
#   * `next_update` is always passed READY-MADE by
#     services/support_comms.next_update_note(). No template composes a time, so an invented
#     SLA is impossible here rather than merely discouraged.

SENDER_SUPPORT = "Zoiko Steam Support"


def support_case_url(ticket_id: str) -> str:
    """The customer's own support view for one case."""
    return f"{public_base_url()}/organization/support/{ticket_id}"


def _sup_send(to, subject, header, headline, preheader, rows, body, cta, url, footer):
    return _send(
        to, subject,
        _org_shell(header, headline, preheader, rows, html.escape(body), cta, url, footer),
        _org_text(headline, preheader, rows, body, cta, url, footer, SENDER_SUPPORT),
        sender=_sender_identity(SENDER_SUPPORT),
    )


SUP_CTA = "View support case"
SUP_PREHEADER = "An update on your Zoiko Steam support case."


def _case_rows(case_reference, subject, category, priority, status, owner, org_name):
    """The identity block every SUP message carries. No internal routing metadata."""
    return [("Case reference", case_reference), ("Subject", subject),
            ("Category", category), ("Priority", priority),
            ("Status", status), ("Handled by", owner), ("Organization", org_name)]


# -- SUP-001 Support case opened ----------------------------------------------------------

SUP_001_SUBJECT = "Zoiko Steam support case {case} was opened"


def send_support_case_opened_email(to, *, name, case_reference, subject, category, priority,
                                   status, owner, next_update, case_url, org_name,
                                   created_at, requester, next_action):
    body = (f"Hi {name}, we have opened support case {case_reference} and our team will "
            f"pick it up.")
    rows = _case_rows(case_reference, subject, category, priority, status, owner, org_name)
    rows += [("Opened", created_at), ("Raised by", requester),
             ("What happens next", next_action)]
    return _sup_send(to, SUP_001_SUBJECT.format(case=case_reference), "Support case",
                     "Your support case is open.", SUP_PREHEADER, rows, body,
                     SUP_CTA, case_url, next_update)


# -- SUP-002 Update and customer action ---------------------------------------------------

SUP_002_UPDATE_SUBJECT = "Update on support case {case}"
SUP_002_ACTION_SUBJECT = "Action required on support case {case}"
SUP_002_REMINDER_SUBJECT = "Reminder: action needed on support case {case}"


def send_support_case_update_email(to, *, name, case_reference, subject, category, priority,
                                   status, owner, next_update, case_url, org_name,
                                   updated_at, update_text, action_required):
    """`update_text` is SupportTicket.customer_update - the only free text mailed here.
    internal_notes has no parameter and therefore no route into this message."""
    body = f"Hi {name}, there is an update on support case {case_reference}."
    rows = _case_rows(case_reference, subject, category, priority, status, owner, org_name)
    rows += [("Updated", updated_at), ("Update", update_text),
             ("Action needed from you", "Yes" if action_required else "No")]
    return _sup_send(to, SUP_002_UPDATE_SUBJECT.format(case=case_reference), "Support case",
                     "Your support case was updated.", SUP_PREHEADER, rows, body,
                     SUP_CTA, case_url, next_update)


def send_support_action_required_email(to, *, name, case_reference, subject, category,
                                       priority, status, owner, next_update, case_url,
                                       org_name, requested_action, due_at, secure_note):
    body = (f"Hi {name}, we need something from you before we can continue with support "
            f"case {case_reference}.")
    rows = _case_rows(case_reference, subject, category, priority, status, owner, org_name)
    rows.append(("What we need", requested_action))
    # A due date appears only when one was actually recorded.
    if due_at:
        rows.append(("Needed by", due_at))
    return _sup_send(to, SUP_002_ACTION_SUBJECT.format(case=case_reference), "Support case",
                     "We need something from you.", SUP_PREHEADER, rows, body,
                     SUP_CTA, case_url, secure_note)


def send_support_action_reminder_email(to, *, name, case_reference, subject, category,
                                       priority, status, owner, next_update, case_url,
                                       org_name, requested_action, due_at):
    body = (f"Hi {name}, support case {case_reference} is still waiting on something from "
            f"you.")
    rows = _case_rows(case_reference, subject, category, priority, status, owner, org_name)
    rows.append(("Still needed", requested_action))
    if due_at:
        rows.append(("Needed by", due_at))
    return _sup_send(to, SUP_002_REMINDER_SUBJECT.format(case=case_reference),
                     "Support case", "Your support case is waiting on you.", SUP_PREHEADER,
                     rows, body, SUP_CTA, case_url,
                     "Add what we need in the support case and we will continue.")


# -- SUP-003 Escalation -------------------------------------------------------------------

SUP_003_ESCALATED_SUBJECT = "Support case {case} was escalated"
SUP_003_OWNER_SUBJECT = "The owner changed for support case {case}"
SUP_003_INCIDENT_SUBJECT = "Support case {case} is linked to an active incident"


def send_support_escalated_email(to, *, name, case_reference, subject, category, priority,
                                 status, owner, next_update, case_url, org_name,
                                 escalated_at, reason):
    body = (f"Hi {name}, we have escalated support case {case_reference} so it gets more "
            f"attention.")
    rows = _case_rows(case_reference, subject, category, priority, status, owner, org_name)
    rows += [("Escalated", escalated_at), ("Why", reason)]
    # `next_update` is ready-made: either a stored commitment or the honest fallback.
    return _sup_send(to, SUP_003_ESCALATED_SUBJECT.format(case=case_reference),
                     "Support case", "Your support case was escalated.", SUP_PREHEADER,
                     rows, body, SUP_CTA, case_url, next_update)


def send_support_owner_changed_email(to, *, name, case_reference, subject, category,
                                     priority, status, owner, next_update, case_url,
                                     org_name, previous_owner, current_owner, effective_at):
    body = (f"Hi {name}, support case {case_reference} is now being handled by a different "
            f"team.")
    rows = _case_rows(case_reference, subject, category, priority, status, owner, org_name)
    rows += [("Previous", previous_owner), ("Current", current_owner),
             ("Effective", effective_at)]
    return _sup_send(to, SUP_003_OWNER_SUBJECT.format(case=case_reference), "Support case",
                     "Your support case changed hands.", SUP_PREHEADER, rows, body,
                     SUP_CTA, case_url, next_update)


def send_support_incident_linked_email(to, *, name, case_reference, subject, category,
                                       priority, status, owner, next_update, case_url,
                                       org_name, incident_reference, incident_status,
                                       impact):
    """Only the approved incident reference, its safe status and a generic impact line.

    There is no parameter for the incident's detail, its commander, its severity or any
    root-cause text, so none of that can be mailed from here.
    """
    body = (f"Hi {name}, support case {case_reference} is linked to a wider incident we are "
            f"already working on.")
    rows = _case_rows(case_reference, subject, category, priority, status, owner, org_name)
    rows += [("Incident reference", incident_reference),
             ("Incident status", incident_status), ("Current impact", impact)]
    return _sup_send(to, SUP_003_INCIDENT_SUBJECT.format(case=case_reference),
                     "Support case", "Your case is linked to an incident.", SUP_PREHEADER,
                     rows, body, SUP_CTA, case_url, next_update)


# -- SUP-004 Resolution, close, reopen, feedback ------------------------------------------

SUP_004_RESOLVED_SUBJECT = "Support case {case} was resolved"
SUP_004_CLOSED_SUBJECT = "Support case {case} was closed"
SUP_004_REOPENED_SUBJECT = "Support case {case} was reopened"
SUP_004_FEEDBACK_SUBJECT = "Tell us about your Zoiko Steam support experience"


def send_support_resolved_email(to, *, name, case_reference, subject, category, priority,
                                status, owner, next_update, case_url, org_name,
                                resolved_at, summary, action_remains, reopen_note):
    body = f"Hi {name}, support case {case_reference} has been resolved."
    rows = _case_rows(case_reference, subject, category, priority, status, owner, org_name)
    rows += [("Resolved", resolved_at), ("What we did", summary),
             ("Anything left for you", "Yes" if action_remains else "No")]
    return _sup_send(to, SUP_004_RESOLVED_SUBJECT.format(case=case_reference),
                     "Support case", "Your support case is resolved.", SUP_PREHEADER,
                     rows, body, SUP_CTA, case_url, reopen_note)


def send_support_closed_email(to, *, name, case_reference, subject, category, priority,
                              status, owner, next_update, case_url, org_name,
                              closed_at, summary, reopen_note):
    body = f"Hi {name}, support case {case_reference} is now closed."
    rows = _case_rows(case_reference, subject, category, priority, status, owner, org_name)
    rows += [("Closed", closed_at), ("Summary", summary)]
    return _sup_send(to, SUP_004_CLOSED_SUBJECT.format(case=case_reference), "Support case",
                     "Your support case is closed.", SUP_PREHEADER, rows, body,
                     SUP_CTA, case_url, reopen_note)


def send_support_reopened_email(to, *, name, case_reference, subject, category, priority,
                                status, owner, next_update, case_url, org_name,
                                reopened_at, next_step):
    body = f"Hi {name}, support case {case_reference} has been reopened."
    rows = _case_rows(case_reference, subject, category, priority, status, owner, org_name)
    rows += [("Reopened", reopened_at), ("Next step", next_step)]
    return _sup_send(to, SUP_004_REOPENED_SUBJECT.format(case=case_reference),
                     "Support case", "Your support case was reopened.", SUP_PREHEADER,
                     rows, body, SUP_CTA, case_url, next_update)


def send_support_feedback_email(to, *, name, case_reference, subject, category, priority,
                                status, owner, next_update, case_url, org_name,
                                resolved_at, consent_note):
    """Sent only for cases services/support_comms.is_feedback_eligible() approves.

    Carries no promotional content and no marketing link - answering it subscribes the
    recipient to nothing, which the footer states plainly.
    """
    body = (f"Hi {name}, support case {case_reference} is closed out. How did we do?")
    rows = [("Case reference", case_reference), ("Subject", subject),
            ("Organization", org_name), ("Closed", resolved_at)]
    return _sup_send(to, SUP_004_FEEDBACK_SUBJECT, "Support feedback",
                     "How was your support experience?", SUP_PREHEADER, rows, body,
                     "Give feedback", case_url, consent_note)


# == SEC-001 .. SEC-006 - security, abuse and content restriction ========================
# Security-facing. Every CTA is /organization/security - the customer Security Center,
# never /admin and never a Super Admin path.
#
# Structural guarantees, enforced by the parameter lists below. NO template here accepts:
#   a password, a reset token, an MFA secret, a recovery code, an API key, a webhook signing
#   secret, a LiveKit credential, a stream key, a raw log line, an IP address, a detection
#   rule or signature, a risk score or threshold, an exploit or vulnerability detail,
#   a reporter identity, or an internal admin URL.
# There is no parameter through which any of those could travel.
#
# Containment and next-update wording is always passed READY-MADE by
# services/security_comms (containment_note / disclosure_next_update), so no template can
# compose a containment claim or invent an ETA.

SENDER_SECURITY_OPS = "Zoiko Steam Security"


def security_center_url() -> str:
    return f"{public_base_url()}/organization/security"


def _sec_send(to, subject, header, headline, preheader, rows, body, cta, url, footer):
    return _send(
        to, subject,
        _org_shell(header, headline, preheader, rows, html.escape(body), cta, url, footer),
        _org_text(headline, preheader, rows, body, cta, url, footer, SENDER_SECURITY_OPS),
        sender=_sender_identity(SENDER_SECURITY_OPS),
    )


SEC_CTA = "Open Security Center"
SEC_PREHEADER = "A security notice for your Zoiko Steam organization."
# Repeated deliberately: the single most useful anti-phishing line we can carry, given these
# are exactly the messages an attacker would imitate.
NEVER_ASK = ("Zoiko Steam never asks for your password, recovery codes, MFA codes or API "
             "keys by email.")


# -- SEC-006 Security contact verification ------------------------------------------------

SEC_006_VERIFY_SUBJECT = "Verify your Zoiko Steam security contact"
SEC_006_VIOLATION_SUBJECT = "Security action required for your Zoiko Steam account"


def send_security_contact_verify_email(to, *, name, org_name, reason, scope, expires_at,
                                       verify_url):
    """Carries no incident detail and no organization secrets - it only establishes the
    channel that SEC-001 and SEC-003 later depend on."""
    body = (f"Hi {name}, please confirm this address so it can receive security "
            f"notifications for {org_name}.")
    rows = [("Organization", org_name), ("Why you received this", reason),
            ("What this contact receives", scope), ("Link expires", expires_at)]
    return _sec_send(to, SEC_006_VERIFY_SUBJECT, "Security contact",
                     "Verify your security contact.", SEC_PREHEADER, rows, body,
                     "Verify security contact", verify_url, NEVER_ASK)


def send_security_violation_email(to, *, name, account, org_name, occurred_at, category,
                                  summary, access_restricted, remediation, security_url):
    """A CONFIRMED violation only. There is no parameter for a rule, a threshold, a
    signature or an address, so detection internals cannot appear."""
    body = (f"Hi {name}, a security policy condition was confirmed on {account}. "
            f"{summary}")
    rows = [("Account", account), ("Organization", org_name), ("When", occurred_at),
            ("What happened", category),
            ("Access restricted", "Yes" if access_restricted else "No"),
            ("What you need to do", remediation)]
    return _sec_send(to, SEC_006_VIOLATION_SUBJECT, "Security", "Security action required.",
                     SEC_PREHEADER, rows, body, SEC_CTA, security_url, NEVER_ASK)


# -- SEC-001 Urgent security alert --------------------------------------------------------

SEC_001_ALERT_SUBJECT = "Urgent security alert for your Zoiko Steam account"
SEC_001_CONTAINED_SUBJECT = "Security event contained for your Zoiko Steam account"
SEC_001_RESOLVED_SUBJECT = "Security event resolved for your Zoiko Steam account"


def send_security_alert_email(to, *, name, variant, reference, account, org_name, category,
                              confirmed_at, contained_at, resolved_at, containment,
                              access_state, summary, remediation, security_url,
                              recovery_note):
    """Sent only from a CONFIRMED (or later) SecurityEvent.

    `containment` arrives ready-made from containment_note(), which reads `contained_at`
    alone - so the containment sentence cannot be produced by a token revocation or a
    password change.
    """
    subject = {"confirmed": SEC_001_ALERT_SUBJECT,
               "contained": SEC_001_CONTAINED_SUBJECT,
               "resolved": SEC_001_RESOLVED_SUBJECT}[variant]
    headline = {"confirmed": "We confirmed a high-risk security event.",
                "contained": "The security event has been contained.",
                "resolved": "The security event is resolved."}[variant]
    body = (f"Hi {name}, {summary}")
    rows = [("Reference", reference), ("Account", account), ("Organization", org_name),
            ("What we confirmed", category), ("Confirmed", confirmed_at),
            ("Current access state", access_state)]
    if variant == "confirmed":
        rows.append(("Status", containment))
    if contained_at:
        rows.append(("Contained", contained_at))
    # RESOLVED is reported as its own distinct fact, never merged with containment.
    if resolved_at:
        rows.append(("Resolved", resolved_at))
    rows.append(("What you need to do", remediation))
    footer = NEVER_ASK + " " + recovery_note
    return _sec_send(to, subject, "Security alert", headline, SEC_PREHEADER, rows, body,
                     SEC_CTA, security_url, footer)


# -- SEC-002 Break-glass lifecycle --------------------------------------------------------

SEC_002_STARTED_SUBJECT = "Emergency access started for your Zoiko Steam organization"
SEC_002_ENDED_SUBJECT = "Emergency access ended for your Zoiko Steam organization"
SEC_002_REVIEW_SUBJECT = "Action required: emergency-access review is overdue"


def send_breakglass_started_email(to, *, name, org_name, reference, reason, scope, operator,
                                  approval, security_url, started_at, expires_at):
    """`expires_at` is the elevation's real expiry, which services/ops.current_elevation
    enforces - the email quotes an enforced deadline, it does not assert one."""
    body = (f"Hi {name}, a Zoiko Steam engineer has started emergency access to {org_name} "
            f"under independent authorization.")
    rows = [("Organization", org_name), ("Reference", reference), ("Started", started_at),
            ("Access expires", expires_at), ("Purpose", reason),
            ("Approved scope", scope), ("Operator", operator),
            ("Independent approval", approval)]
    return _sec_send(to, SEC_002_STARTED_SUBJECT, "Emergency access",
                     "Emergency access has started.", SEC_PREHEADER, rows, body,
                     SEC_CTA, security_url,
                     "Access ends automatically at the time above, and every action is "
                     "recorded in your audit trail.")


def send_breakglass_ended_email(to, *, name, org_name, reference, reason, scope, operator,
                                approval, security_url, started_at, ended_at, duration,
                                end_reason, review_note):
    body = (f"Hi {name}, the emergency access session on {org_name} has ended.")
    rows = [("Organization", org_name), ("Reference", reference), ("Started", started_at),
            ("Ended", ended_at), ("Duration", duration), ("How it ended", end_reason),
            ("Approved scope", scope), ("Operator", operator), ("Review", review_note)]
    # Access ending is not a finding that the activity was appropriate. The footer says so.
    return _sec_send(to, SEC_002_ENDED_SUBJECT, "Emergency access",
                     "Emergency access has ended.", SEC_PREHEADER, rows, body,
                     SEC_CTA, security_url,
                     "Ending the session does not itself conclude that the activity was "
                     "appropriate - that is what the review determines.")


def send_breakglass_review_email(to, *, name, org_name, reference, reason, scope, operator,
                                 approval, security_url, due_at):
    body = (f"Hi {name}, the review of the emergency access session on {org_name} is "
            f"overdue.")
    rows = [("Organization", org_name), ("Reference", reference),
            ("Review was due", due_at), ("Approved scope", scope), ("Operator", operator),
            ("Independent approval", approval)]
    return _sec_send(to, SEC_002_REVIEW_SUBJECT, "Emergency access",
                     "An emergency-access review is overdue.", SEC_PREHEADER, rows, body,
                     SEC_CTA, security_url,
                     "Complete the review in your Security Center so the session can be "
                     "closed out.")


# -- SEC-003 Organization security incident -----------------------------------------------

SEC_003_OPENED_SUBJECT = "Security incident opened for your Zoiko Steam organization"
SEC_003_UPDATE_SUBJECT = "Update on the security incident for your Zoiko Steam organization"
SEC_003_CONTAINED_SUBJECT = "Security incident contained for your Zoiko Steam organization"
SEC_003_RESOLVED_SUBJECT = "Security incident resolved for your Zoiko Steam organization"


def send_security_incident_email(to, *, name, variant, reference, org_name, status,
                                 opened_at, contained_at, resolved_at, affected_service,
                                 impact, action, resolution, report_available, next_update,
                                 evidence_note, security_url):
    """Customer-safe disclosure fields only.

    There is no parameter for the incident's internal detail, its commander, its severity,
    an RCA draft, a detector rule, an attack payload or a log line - so none of them can be
    mailed. Evidence is viewed behind authentication, never attached or quoted.
    """
    subject = {"incident_opened": SEC_003_OPENED_SUBJECT,
               "incident_update": SEC_003_UPDATE_SUBJECT,
               "incident_contained": SEC_003_CONTAINED_SUBJECT,
               "incident_resolved": SEC_003_RESOLVED_SUBJECT}[variant]
    headline = {"incident_opened": "We opened a security incident.",
                "incident_update": "There is an update on the security incident.",
                "incident_contained": "The security incident has been contained.",
                "incident_resolved": "The security incident is resolved."}[variant]
    body = f"Hi {name}, {impact}"
    rows = [("Incident reference", reference), ("Organization", org_name),
            ("Status", status), ("Opened", opened_at),
            ("Affected service", affected_service), ("Impact on you", impact),
            ("Recommended action", action)]
    if contained_at:
        rows.append(("Contained", contained_at))
    if variant == "incident_contained":
        rows.append(("Investigation", "Our investigation is continuing."))
    if resolved_at:
        rows.append(("Resolved", resolved_at))
    if variant == "incident_resolved":
        rows.append(("Resolution", resolution or "Our review is complete."))
        # Only mentioned when an approved artifact genuinely exists.
        rows.append(("Report", "A report is available in your Security Center."
                     if report_available else "No customer report was produced."))
    if variant in ("incident_opened", "incident_update"):
        rows.append(("Next update", next_update))
    return _sec_send(to, subject, "Security incident", headline, SEC_PREHEADER, rows, body,
                     "View secure incident details", security_url,
                     evidence_note + " " + NEVER_ASK)


# -- SEC-004 Abuse report lifecycle -------------------------------------------------------

SEC_004_RECEIVED_SUBJECT = "We received your Zoiko Steam report"
SEC_004_CLOSED_SUBJECT = "Our review of your Zoiko Steam report is complete"


def send_abuse_received_email(to, *, name, reference, received_at, category, promise,
                              consent_note, security_url):
    """Acknowledges receipt WITHOUT promising an enforcement outcome.

    `promise` is the fixed REVIEW_PROMISE constant; there is no parameter for a suspension,
    a removal, a ban, legal action or a refund, so none can be offered from here.
    """
    body = (f"Hi {name}, thank you for the report. {promise}")
    rows = [("Report reference", reference), ("Received", received_at),
            ("Reported category", category), ("What happens next", promise)]
    return _sec_send(to, SEC_004_RECEIVED_SUBJECT, "Report received",
                     "We received your report.", SEC_PREHEADER, rows, body,
                     "Check report status", security_url, consent_note)


def send_abuse_closed_email(to, *, name, reference, closed_at, outcome, security_url):
    """`outcome` is one of four approved closure notes, none of which names an enforcement
    action taken against anybody."""
    body = f"Hi {name}, our review of report {reference} is complete. {outcome}"
    rows = [("Report reference", reference), ("Review completed", closed_at),
            ("Outcome", outcome)]
    return _sec_send(to, SEC_004_CLOSED_SUBJECT, "Report closed",
                     "Our review is complete.", SEC_PREHEADER, rows, body,
                     "Open Security Center", security_url,
                     "We do not share the details of any action we take on individual "
                     "accounts.")


# -- SEC-005 Content restriction and appeal -----------------------------------------------

SEC_005_RESTRICTED_SUBJECT = "Access to Zoiko Steam content was restricted"
SEC_005_APPEAL_RECEIVED_SUBJECT = "We received your appeal"
SEC_005_APPEAL_DECIDED_SUBJECT = "Decision on your Zoiko Steam content appeal"
SEC_005_REMOVED_SUBJECT = "Content removal completed"


def send_content_restriction_email(to, *, name, reference, org_name, content,
                                   restriction_type, effective_at, reason, appeal_allowed,
                                   appeal_deadline, security_url):
    """No parameter carries a complainant, so the reporter cannot be identified here."""
    body = (f"Hi {name}, access to {content} in {org_name} has been restricted. {reason}")
    rows = [("Reference", reference), ("Organization", org_name), ("Content", content),
            ("Restriction", restriction_type), ("Effective", effective_at),
            ("Reason", reason),
            ("Appeal available", "Yes" if appeal_allowed else "No")]
    if appeal_allowed and appeal_deadline:
        rows.append(("Appeal by", appeal_deadline))
    cta = "Appeal this decision" if appeal_allowed else SEC_CTA
    return _sec_send(to, SEC_005_RESTRICTED_SUBJECT, "Content restricted",
                     "Access to your content was restricted.", SEC_PREHEADER, rows, body,
                     cta, security_url,
                     "We do not share who raised a report about content.")


def send_restriction_appeal_email(to, *, name, variant, reference, org_name, content,
                                  submitted_at, decided_at, decision, current_state,
                                  next_step, security_url):
    subject = {"appeal_received": SEC_005_APPEAL_RECEIVED_SUBJECT,
               "appeal_upheld": SEC_005_APPEAL_DECIDED_SUBJECT,
               "appeal_granted": SEC_005_APPEAL_DECIDED_SUBJECT}[variant]
    headline = {"appeal_received": "We received your appeal.",
                "appeal_upheld": "Your appeal was reviewed.",
                "appeal_granted": "Your appeal was granted."}[variant]
    body = {
        "appeal_received": (f"Hi {name}, we have your appeal about {content} and it is "
                            f"queued for review."),
        "appeal_upheld": (f"Hi {name}, we reviewed your appeal about {content}. "
                          f"{decision}"),
        "appeal_granted": (f"Hi {name}, we reviewed your appeal about {content} and the "
                           f"restriction has been lifted."),
    }[variant]
    rows = [("Restriction reference", reference), ("Organization", org_name),
            ("Content", content), ("Appeal submitted", submitted_at)]
    if variant == "appeal_received":
        rows += [("Current status", current_state), ("Next step", next_step)]
    else:
        rows += [("Decided", decided_at), ("Decision", decision),
                 ("Current status", current_state), ("Next step", next_step)]
    return _sec_send(to, subject, "Content appeal", headline, SEC_PREHEADER, rows, body,
                     SEC_CTA, security_url,
                     "We do not share who raised a report about content.")


def send_content_removed_email(to, *, name, reference, org_name, content, completed_at,
                               residual, security_url):
    """`residual` is generated by removal_position(), which reads MED-011 retention and
    legal-hold state - so this never claims that every copy everywhere is gone."""
    body = (f"Hi {name}, the removal of {content} in {org_name} has completed.")
    rows = [("Reference", reference), ("Organization", org_name), ("Content", content),
            ("Completed", completed_at), ("What this means", residual)]
    return _sec_send(to, SEC_005_REMOVED_SUBJECT, "Content removed",
                     "Content removal is complete.", SEC_PREHEADER, rows, body,
                     SEC_CTA, security_url, residual)


# == PRV-001 .. PRV-004 - privacy and data governance ====================================
# Every CTA routes to /organization/privacy - the customer Privacy Center, never /admin.
#
# Structural guarantees enforced by the parameter lists below. NO template here accepts a
# password, a password hash, an MFA secret, a recovery code, an API key, a security log, raw
# audit evidence, legal advice, representative evidence, another user's data, or the export
# itself. There is no parameter through which any of those could travel, and the export is
# carried as a LINK only.
#
# Deadline wording always arrives ready-made from privacy_comms.deadline_note(), and the
# deletion residue sentence from deletion_sentence(), so no template can invent a statutory
# date or claim total erasure.

SENDER_PRIVACY = "Zoiko Steam Privacy"


def privacy_center_url() -> str:
    return f"{public_base_url()}/organization/privacy"


def _prv_send(to, subject, header, headline, preheader, rows, body, cta, url, footer):
    return _send(
        to, subject,
        _org_shell(header, headline, preheader, rows, html.escape(body), cta, url, footer),
        _org_text(headline, preheader, rows, body, cta, url, footer, SENDER_PRIVACY),
        sender=_sender_identity(SENDER_PRIVACY),
    )


PRV_CTA = "Open Privacy Center"
PRV_PREHEADER = "About your Zoiko Steam privacy request."
NEVER_ASK_PRV = ("Zoiko Steam never asks for your password, recovery codes, MFA codes or "
                 "API keys by email.")


def _request_rows(reference, request_type, status):
    """The identity block. Deliberately does NOT restate the requester's own free-text
    details back to them - the reference and type are enough to identify the request."""
    return [("Request reference", reference), ("Request type", request_type),
            ("Current status", status)]


# -- PRV-001 Intake and verification ------------------------------------------------------

PRV_001_RECEIVED_SUBJECT = "We received your Zoiko Steam privacy request"
PRV_001_VERIFY_SUBJECT = "Action required: verify your Zoiko Steam privacy request"


def send_privacy_received_email(to, *, name, reference, request_type, status,
                                deadline_note, privacy_url, received_at,
                                verification_required):
    body = (f"Hi {name}, we have your privacy request and it is logged as {reference}.")
    rows = _request_rows(reference, request_type, status)
    rows += [("Received", received_at),
             ("Identity verification", "Required before we act"
              if verification_required else "Not required"),
             ("Timing", deadline_note)]
    return _prv_send(to, PRV_001_RECEIVED_SUBJECT, "Privacy request",
                     "We received your privacy request.", PRV_PREHEADER, rows, body,
                     PRV_CTA, privacy_url, NEVER_ASK_PRV)


def send_privacy_verify_email(to, *, name, reference, request_type, status, deadline_note,
                              privacy_url, expires_at, verify_url, purpose_note):
    """Discloses only that verification is needed - never the request's contents."""
    body = (f"Hi {name}, before we act on request {reference} we need to confirm your "
            f"identity. {purpose_note}")
    rows = _request_rows(reference, request_type, status)
    rows += [("Why", purpose_note), ("Link expires", expires_at)]
    return _prv_send(to, PRV_001_VERIFY_SUBJECT, "Privacy request",
                     "Please verify your privacy request.", PRV_PREHEADER, rows, body,
                     "Verify my request", verify_url, NEVER_ASK_PRV)


# -- PRV-002 Status, clarification, extension, decision -----------------------------------

PRV_002_STATUS_SUBJECT = "Update on privacy request {reference}"
PRV_002_CLARIFY_SUBJECT = "Action required for privacy request {reference}"
PRV_002_EXTENSION_SUBJECT = "Privacy request {reference} deadline was extended"
PRV_002_DECISION_SUBJECT = "Decision on privacy request {reference}"


def send_privacy_status_email(to, *, name, reference, request_type, status, deadline_note,
                              privacy_url, updated_at, action_required):
    body = f"Hi {name}, there is an update on privacy request {reference}."
    rows = _request_rows(reference, request_type, status)
    rows += [("Updated", updated_at), ("Timing", deadline_note),
             ("Action needed from you", "Yes" if action_required else "No")]
    return _prv_send(to, PRV_002_STATUS_SUBJECT.format(reference=reference),
                     "Privacy request", "Your privacy request was updated.", PRV_PREHEADER,
                     rows, body, PRV_CTA, privacy_url, NEVER_ASK_PRV)


def send_privacy_clarification_email(to, *, name, reference, request_type, status,
                                     deadline_note, privacy_url, needed, due_at,
                                     secure_note):
    body = (f"Hi {name}, we need a little more information before we can continue with "
            f"request {reference}.")
    rows = _request_rows(reference, request_type, status)
    rows.append(("What we need", needed))
    # A response date appears only when one was actually recorded.
    if due_at:
        rows.append(("Please reply by", due_at))
    rows.append(("Timing", deadline_note))
    return _prv_send(to, PRV_002_CLARIFY_SUBJECT.format(reference=reference),
                     "Privacy request", "We need more information.", PRV_PREHEADER, rows,
                     body, "Respond securely", privacy_url, secure_note)


def send_privacy_extension_email(to, *, name, reference, request_type, status,
                                 deadline_note, privacy_url, original_deadline,
                                 new_deadline, reason):
    """Only reachable when a real deadline existed and an authorized extension was
    recorded - `reason` is one of the approved categories, never assumed."""
    body = (f"Hi {name}, we need more time to complete privacy request {reference}.")
    rows = _request_rows(reference, request_type, status)
    rows += [("Original due date", original_deadline), ("New due date", new_deadline),
             ("Why", reason)]
    return _prv_send(to, PRV_002_EXTENSION_SUBJECT.format(reference=reference),
                     "Privacy request", "We extended the due date.", PRV_PREHEADER, rows,
                     body, PRV_CTA, privacy_url, NEVER_ASK_PRV)


def send_privacy_decision_email(to, *, name, reference, request_type, status,
                                deadline_note, privacy_url, outcome, reason, summary,
                                review_route):
    """`reason` is an approved customer-safe category and `summary` is operator-authored
    customer-facing text. There is no parameter for internal legal reasoning."""
    body = f"Hi {name}, we have reached a decision on privacy request {reference}. {summary}"
    rows = _request_rows(reference, request_type, status)
    rows += [("Outcome", outcome), ("Reason", reason), ("What this means", summary)]
    return _prv_send(to, PRV_002_DECISION_SUBJECT.format(reference=reference),
                     "Privacy request", "Decision on your privacy request.", PRV_PREHEADER,
                     rows, body, PRV_CTA, privacy_url, review_route)


# -- PRV-003 Export and deletion ----------------------------------------------------------

PRV_003_EXPORT_SUBJECT = "Your Zoiko Steam privacy export is ready"
PRV_003_EXPIRED_SUBJECT = "Your Zoiko Steam privacy export link expired"
PRV_003_DELETED_SUBJECT = "Your Zoiko Steam deletion request was completed"
PRV_003_DELETION_UPDATE_SUBJECT = "Update on your Zoiko Steam deletion request"


def send_privacy_export_ready_email(to, *, name, reference, request_type, status,
                                    deadline_note, privacy_url, generated_at, expires_at,
                                    download_url):
    """Carries a short-lived LINK. There is no attachment parameter, so the export itself
    cannot travel by email."""
    body = (f"Hi {name}, the copy of your personal data for request {reference} is ready "
            f"to download.")
    rows = _request_rows(reference, request_type, status)
    rows += [("Generated", generated_at), ("Link expires", expires_at)]
    return _prv_send(to, PRV_003_EXPORT_SUBJECT, "Privacy export",
                     "Your privacy export is ready.", PRV_PREHEADER, rows, body,
                     "Download my data", download_url,
                     "The link is personal to you, works once and expires shortly. "
                     + NEVER_ASK_PRV)


def send_privacy_export_expired_email(to, *, name, reference, request_type, status,
                                      deadline_note, privacy_url, expired_at,
                                      regenerate_note):
    body = (f"Hi {name}, the download link for privacy request {reference} has expired.")
    rows = _request_rows(reference, request_type, status)
    rows += [("Link expired", expired_at), ("What to do", regenerate_note)]
    return _prv_send(to, PRV_003_EXPIRED_SUBJECT, "Privacy export",
                     "Your export link expired.", PRV_PREHEADER, rows, body,
                     PRV_CTA, privacy_url, NEVER_ASK_PRV)


def send_privacy_deletion_email(to, *, name, variant, reference, request_type, status,
                                deadline_note, privacy_url, state, completed_at,
                                access_note, residual, blocker, next_action):
    """`residual` arrives ready-made from deletion_sentence(), which is generated from the
    recorded residual categories - so this cannot claim total erasure."""
    completed = variant == "completed"
    subject = PRV_003_DELETED_SUBJECT if completed else PRV_003_DELETION_UPDATE_SUBJECT
    headline = ("Your deletion request is complete." if completed
                else "An update on your deletion request.")
    body = (f"Hi {name}, {residual}")
    rows = _request_rows(reference, request_type, status)
    rows.append(("Deletion state", state))
    if completed_at:
        rows.append(("Completed", completed_at))
    rows += [("Account access", access_note), ("What we retained", residual)]
    if blocker:
        rows.append(("Why some data remains", blocker))
    rows.append(("Next step", next_action))
    return _prv_send(to, subject, "Deletion request", headline, PRV_PREHEADER, rows, body,
                     PRV_CTA, privacy_url,
                     "We only keep what we are required to keep, and only for as long as "
                     "we must.")


# -- PRV-004 Notice, consent, subprocessors, retention exception --------------------------

PRV_004_NOTICE_SUBJECT = "Zoiko Steam privacy notice was updated"
PRV_004_CONSENT_SUBJECT = "Action required: your choice about the Zoiko Steam privacy notice"
PRV_004_SUBPROCESSOR_SUBJECT = "Zoiko Steam subprocessor list was updated"
PRV_004_RETENTION_SUBJECT = "Update on privacy request {reference}"


def send_privacy_notice_email(to, *, name, version, effective_at, summary,
                              consent_required, consent_purpose, options, notice_url,
                              privacy_url):
    """When consent is required, Accept and Decline are rendered as EQUAL peers with
    nothing preselected - `options` comes from consent_options()."""
    subject = PRV_004_CONSENT_SUBJECT if consent_required else PRV_004_NOTICE_SUBJECT
    headline = ("Please tell us your choice." if consent_required
                else "We updated our privacy notice.")
    body = (f"Hi {name}, {summary}")
    rows = [("Notice version", version), ("Effective", effective_at),
            ("What changed", summary)]
    if consent_required:
        rows += [("Your choice is needed for", consent_purpose or "the purpose described"),
                 # Equal weight, neither preselected, neither hidden.
                 ("Your options", " or ".join(o["label"] for o in options)),
                 ("Preselected", "Nothing is preselected - the choice is yours")]
    cta = "Review and choose" if consent_required else "View privacy notice"
    footer = ("You can accept or decline. Declining will not affect anything we do not "
              "need your consent for." if consent_required else
              "You can read the full notice at any time in your Privacy Center.")
    return _prv_send(to, subject, "Privacy notice", headline, PRV_PREHEADER, rows, body,
                     cta, notice_url, footer)


def send_subprocessor_notice_email(to, *, name, processor, service, purpose, status,
                                   effective_at, list_url, privacy_url):
    """Names a real processor and its purpose. No contract or confidential vendor terms."""
    body = (f"Hi {name}, we have updated the list of processors that help us run Zoiko "
            f"Steam.")
    rows = [("Processor", processor), ("Service", service), ("Purpose", purpose),
            ("Status", status), ("Effective", effective_at)]
    return _prv_send(to, PRV_004_SUBPROCESSOR_SUBJECT, "Subprocessors",
                     "Our subprocessor list changed.", PRV_PREHEADER, rows, body,
                     "View subprocessor list", list_url,
                     "We publish who processes data on our behalf and why. We do not "
                     "publish commercial terms.")


def send_privacy_retention_email(to, *, name, reference, request_type, status,
                                 deadline_note, privacy_url, record_type, basis, review_at,
                                 contact_route):
    """States a record CATEGORY and a basis category. No legal advice, and no internal
    retention-policy detail."""
    body = (f"Hi {name}, we could not act fully on request {reference} because some records "
            f"must be retained.")
    rows = _request_rows(reference, request_type, status)
    rows += [("Records retained", record_type), ("Why", basis)]
    if review_at:
        rows.append(("Next review", review_at))
    rows.append(("If you want this reviewed", contact_route))
    return _prv_send(to, PRV_004_RETENTION_SUBJECT.format(reference=reference),
                     "Privacy request", "Some records must be retained.", PRV_PREHEADER,
                     rows, body, PRV_CTA, privacy_url, contact_route)


# == STS-001 .. STS-006 - public status page =============================================
# PUBLIC, unauthenticated audience. Every CTA is /status - the public status page - and a
# manage/unsubscribe link built from an opaque per-subscriber handle, never the address.
#
# Structural guarantees enforced by the parameter lists below. NO template here accepts an
# internal incident detail, a commander, a severity, an RCA draft, a monitoring payload, an
# IP, a detection rule, a credential, a customer identity or an exploit detail. There is no
# parameter through which any of those could travel.
#
# Every timestamp is rendered by billing_date() in UTC, which is the canonical maintenance
# record. Next-update wording always arrives ready-made from next_update_note(), so no
# template can invent an ETA.

SENDER_STATUS = "Zoiko Steam Status"


def public_status_url() -> str:
    return f"{public_base_url()}/status"


def _sts_send(to, subject, header, headline, preheader, rows, body, cta, url, footer):
    return _send(
        to, subject,
        _org_shell(header, headline, preheader, rows, html.escape(body), cta, url, footer),
        _org_text(headline, preheader, rows, body, cta, url, footer, SENDER_STATUS),
        sender=_sender_identity(SENDER_STATUS),
    )


STS_PREHEADER = "An update from the Zoiko Steam status page."
# Repeated on every subscriber message: status is its own communication domain, and somebody
# unsubscribing here must understand they have not opted out of security or billing mail.
STS_SCOPE = ("You are receiving this because you subscribed to Zoiko Steam status updates. "
             "Account, security, billing and privacy emails are separate.")


# -- STS-001 Subscription lifecycle -------------------------------------------------------

STS_001_VERIFY_SUBJECT = "Confirm your Zoiko Steam status subscription"
STS_001_CONFIRMED_SUBJECT = "Your Zoiko Steam status subscription is active"
STS_001_PREFERENCES_SUBJECT = "Your Zoiko Steam status preferences changed"
STS_001_UNSUBSCRIBED_SUBJECT = "You unsubscribed from Zoiko Steam status updates"


def send_status_verify_email(to, *, components, regions, expires_at, confirm_url,
                             status_url):
    body = ("Please confirm this address so we can send you Zoiko Steam status updates.")
    rows = [("Components", _bullets(components)), ("Regions", _bullets(regions)),
            ("Link expires", expires_at)]
    return _sts_send(to, STS_001_VERIFY_SUBJECT, "Status updates",
                     "Confirm your status subscription.", STS_PREHEADER, rows, body,
                     "Confirm subscription", confirm_url,
                     "If you did not request this, ignore it and nothing will be sent.")


def send_status_confirmed_email(to, *, components, regions, notify_kinds, status_url,
                                manage_url):
    body = "Your Zoiko Steam status subscription is active."
    rows = [("Components", _bullets(components)), ("Regions", _bullets(regions)),
            ("You will receive", _bullets(notify_kinds))]
    return _sts_send(to, STS_001_CONFIRMED_SUBJECT, "Status updates",
                     "Your subscription is active.", STS_PREHEADER, rows, body,
                     "Manage preferences", manage_url, STS_SCOPE)


def send_status_preferences_email(to, *, previous_components, previous_regions,
                                  current_components, current_regions, status_url,
                                  manage_url):
    """Only sent for a materially changed selection - update_preferences() returns False
    for a no-op, so an unchanged re-save reaches nothing."""
    body = "Your Zoiko Steam status preferences have been updated."
    rows = [("Previous components", _bullets(previous_components)),
            ("Previous regions", _bullets(previous_regions)),
            ("Current components", _bullets(current_components)),
            ("Current regions", _bullets(current_regions))]
    return _sts_send(to, STS_001_PREFERENCES_SUBJECT, "Status updates",
                     "Your status preferences changed.", STS_PREHEADER, rows, body,
                     "Manage preferences", manage_url, STS_SCOPE)


def send_status_unsubscribed_email(to, *, scope_note, status_url):
    """`scope_note` states explicitly what was NOT affected, so leaving the status list is
    never mistaken for opting out of security or billing mail."""
    body = ("You will no longer receive Zoiko Steam status updates. " + scope_note)
    rows = [("Status updates", "Stopped"), ("What is unaffected", scope_note)]
    return _sts_send(to, STS_001_UNSUBSCRIBED_SUBJECT, "Status updates",
                     "You have unsubscribed.", STS_PREHEADER, rows, body,
                     "Subscribe again", status_url, scope_note)


# -- STS-002 / STS-003 / STS-004 Public incidents -----------------------------------------

STS_002_INVESTIGATING_SUBJECT = "Zoiko Steam is investigating a service issue"
STS_002_IDENTIFIED_SUBJECT = "Cause identified for Zoiko Steam service issue"
STS_003_MONITORING_SUBJECT = "Zoiko Steam service has recovered and is being monitored"
STS_003_RESOLVED_SUBJECT = "Zoiko Steam service incident resolved"
STS_003_REOPENED_SUBJECT = "Zoiko Steam service incident reopened"
STS_003_RESIDUAL_SUBJECT = "Service restored — follow-up work continues"
STS_004_CORRECTION_SUBJECT = "Correction to Zoiko Steam incident update"
STS_004_REVIEW_SUBJECT = "Post-incident review published for {reference}"


def send_status_incident_email(to, *, variant, reference, title, impact, components,
                               regions, started_at, next_update, status_url, manage_url,
                               body, customer_action, identified_at, monitoring_at,
                               resolved_at, reopened_at, residual, review_available):
    """One template across the public incident lifecycle.

    Every field is a published, customer-safe value. There is no parameter for the internal
    incident's detail, its commander, its severity or an RCA draft.
    """
    subject = {
        "incident_investigating": STS_002_INVESTIGATING_SUBJECT,
        "incident_identified": STS_002_IDENTIFIED_SUBJECT,
        "incident_monitoring": STS_003_MONITORING_SUBJECT,
        "incident_resolved": STS_003_RESOLVED_SUBJECT,
        "incident_reopened": STS_003_REOPENED_SUBJECT,
    }[variant]
    headline = {
        "incident_investigating": "We are investigating a service issue.",
        "incident_identified": "We have identified the cause.",
        "incident_monitoring": "Service has recovered and we are monitoring.",
        "incident_resolved": "This incident is resolved.",
        "incident_reopened": "This incident has been reopened.",
    }[variant]
    rows = [("Incident reference", reference), ("Issue", title),
            ("Impact", impact), ("Affected components", _bullets(components)),
            ("Affected regions", _bullets(regions)), ("Started", started_at),
            ("Update", body)]
    if variant == "incident_identified" and identified_at:
        rows.append(("Cause identified", identified_at))
    if variant == "incident_monitoring" and monitoring_at:
        rows.append(("Recovered", monitoring_at))
    if variant == "incident_reopened":
        # The earlier resolution stays visible: it is a published fact, not an error.
        rows.append(("Previously resolved", resolved_at or "Not recorded"))
        rows.append(("Reopened", reopened_at or "Just now"))
    elif variant == "incident_resolved" and resolved_at:
        rows.append(("Resolved", resolved_at))
    if customer_action:
        rows.append(("What you can do", customer_action))
    if residual:
        rows.append(("Follow-up work", residual))
    if variant == "incident_resolved":
        # Mentioned only when an approved review actually exists.
        rows.append(("Post-incident review",
                     "Published on our status page" if review_available
                     else "Not published for this incident"))
    if variant in ("incident_investigating", "incident_identified",
                   "incident_monitoring", "incident_reopened"):
        rows.append(("Next update", next_update))
    return _sts_send(to, subject, "Service status", headline, STS_PREHEADER, rows, body,
                     "View status page", status_url, STS_SCOPE)


def send_status_residual_email(to, *, reference, title, impact, components, regions,
                               started_at, next_update, status_url, manage_url, body,
                               impact_note):
    """Restored service with engineering follow-up. `impact_note` makes clear this is not
    continued customer impact."""
    rows = [("Incident reference", reference), ("Issue", title),
            ("Affected components", _bullets(components)),
            ("Follow-up work", body), ("Your service", impact_note)]
    return _sts_send(to, STS_003_RESIDUAL_SUBJECT, "Service status",
                     "Service restored, follow-up continues.", STS_PREHEADER, rows,
                     impact_note, "View status page", status_url, STS_SCOPE)


def send_status_correction_email(to, *, reference, title, impact, components, regions,
                                 started_at, next_update, status_url, manage_url,
                                 previously_reported, corrected, corrected_at):
    """Shows BOTH statements. `previously_reported` is quoted from the preserved original
    update row, which is never edited or deleted."""
    body = f"We need to correct an earlier update about incident {reference}."
    rows = [("Incident reference", reference), ("Issue", title),
            ("Previously reported", previously_reported),
            ("Corrected", corrected), ("Correction published", corrected_at)]
    return _sts_send(to, STS_004_CORRECTION_SUBJECT, "Service status",
                     "Correction to an earlier update.", STS_PREHEADER, rows, body,
                     "View full incident history", status_url,
                     "Our published history keeps the original update alongside this "
                     "correction. " + STS_SCOPE)


def send_status_review_email(to, *, reference, title, impact, components, regions,
                             started_at, next_update, status_url, manage_url, summary,
                             impact_period, published_at):
    """An APPROVED customer-facing review only. No parameter carries an RCA draft, an
    attack path, exploitable configuration or a staff name."""
    body = f"We have published a post-incident review for {reference}."
    rows = [("Incident reference", reference), ("Issue", title),
            ("Impact period", impact_period), ("Affected components", _bullets(components)),
            ("Summary", summary), ("Published", published_at)]
    return _sts_send(to, STS_004_REVIEW_SUBJECT.format(reference=reference),
                     "Post-incident review", "We published a post-incident review.",
                     STS_PREHEADER, rows, body, "Read the review", status_url, STS_SCOPE)


# -- STS-005 / STS-006 Maintenance --------------------------------------------------------

STS_005_SCHEDULED_SUBJECT = "Scheduled Zoiko Steam maintenance"
STS_005_REMINDER_SUBJECT = "Reminder: upcoming Zoiko Steam maintenance"
STS_005_CHANGED_SUBJECT = "Zoiko Steam scheduled maintenance changed"
STS_005_CANCELED_SUBJECT = "Zoiko Steam scheduled maintenance canceled"
STS_006_STARTED_SUBJECT = "Zoiko Steam scheduled maintenance has started"
STS_006_EXTENDED_SUBJECT = "Zoiko Steam maintenance is taking longer than expected"
STS_006_COMPLETED_SUBJECT = "Zoiko Steam scheduled maintenance is complete"
STS_006_EMERGENCY_SUBJECT = "Emergency Zoiko Steam maintenance is underway"


def send_status_maintenance_email(to, *, variant, reference, title, components, regions,
                                  starts_at, ends_at, impact, emergency, next_update,
                                  status_url, local_note, manage_url, state, started_at,
                                  completed_at, canceled_at, previous_start, previous_end,
                                  previous_impact, emergency_reason, health,
                                  remaining_work):
    """One template across the maintenance lifecycle.

    Emergency work uses its own subject and carries a reason CATEGORY - it is never
    presented as scheduled maintenance. `health` is read from recorded component impact, so
    a completed window never claims the platform is healthy on its own authority.
    """
    if emergency and variant in ("maintenance_scheduled", "maintenance_started"):
        subject = STS_006_EMERGENCY_SUBJECT
        headline = "Emergency maintenance is underway."
    else:
        subject = {
            "maintenance_scheduled": STS_005_SCHEDULED_SUBJECT,
            "maintenance_reminder": STS_005_REMINDER_SUBJECT,
            "maintenance_changed": STS_005_CHANGED_SUBJECT,
            "maintenance_canceled": STS_005_CANCELED_SUBJECT,
            "maintenance_started": STS_006_STARTED_SUBJECT,
            "maintenance_extended": STS_006_EXTENDED_SUBJECT,
            "maintenance_completed": STS_006_COMPLETED_SUBJECT,
        }[variant]
        headline = {
            "maintenance_scheduled": "We have scheduled maintenance.",
            "maintenance_reminder": "Upcoming maintenance.",
            "maintenance_changed": "The maintenance window changed.",
            "maintenance_canceled": "The maintenance is canceled.",
            "maintenance_started": "Maintenance has started.",
            "maintenance_extended": "Maintenance is taking longer than expected.",
            "maintenance_completed": "Maintenance is complete.",
        }[variant]

    body = f"{title} — {impact}"
    rows = [("Maintenance reference", reference), ("Work", title),
            ("Affected components", _bullets(components)),
            ("Affected regions", _bullets(regions))]
    if emergency and emergency_reason:
        rows.append(("Why", emergency_reason))
    rows += [("Starts (UTC)", starts_at), ("Ends (UTC)", ends_at),
             ("Expected impact", impact)]
    if variant == "maintenance_changed":
        # A published schedule is versioned, not silently mutated - both windows are shown.
        rows.insert(4, ("Previous start (UTC)", previous_start or "Unchanged"))
        rows.insert(5, ("Previous end (UTC)", previous_end or "Unchanged"))
        if previous_impact:
            rows.append(("Previous impact", previous_impact))
    if variant == "maintenance_started" and started_at:
        rows.append(("Actually started", started_at))
    if variant == "maintenance_extended":
        rows.append(("Previous expected completion (UTC)", previous_end or "Not recorded"))
        rows.append(("New expected completion (UTC)", ends_at))
    if variant == "maintenance_canceled" and canceled_at:
        rows.append(("Canceled", canceled_at))
    if variant == "maintenance_completed":
        rows.append(("Completed", completed_at or "Just now"))
        # Read from component state, never asserted.
        rows.append(("Current service status", health))
        if remaining_work:
            rows.append(("Remaining work", remaining_work))
    if variant in ("maintenance_scheduled", "maintenance_started",
                   "maintenance_extended", "maintenance_reminder"):
        rows.append(("Next update", next_update))
    rows.append(("Current state", state))
    return _sts_send(to, subject, "Maintenance", headline, STS_PREHEADER, rows, body,
                     "View status page", status_url, local_note + " " + STS_SCOPE)


# ═════════════════════════════════════════════════════════════════════════════════════════
# ZST-EC-001 TRU-001 -> TRU-003 — Trust Center
# ═════════════════════════════════════════════════════════════════════════════════════════
# SECURITY/TRUST class. Sent under its own sender identity, carries no promotional content,
# no tracking, and NO unsubscribe link: an advisory to a verified security contact is not a
# marketing message and a marketing unsubscribe can never suppress it.
#
# Structural guarantees enforced by the parameter lists below. No template in this section
# accepts an exploit payload, a proof of concept, a detection rule, an internal incident
# reference, a commander, a reporter name, a reporter address, a credential, a document's
# contents, or a monitoring payload. There is no parameter through which any of those could
# travel - which is why the test suite asserts on the signatures rather than on the copy.

SENDER_TRUST = "Zoiko Steam Trust"
SENDER_SECURITY_ADVISORY = "Zoiko Steam Security"


def trust_center_url() -> str:
    return f"{public_base_url()}/trust"


def _trust_send(to, subject, header, headline, preheader, rows, body, cta, url, footer,
                sender=SENDER_TRUST):
    return _send(
        to, subject,
        _org_shell(header, headline, preheader, rows, html.escape(body), cta, url, footer),
        _org_text(headline, preheader, rows, body, cta, url, footer, sender),
        sender=_sender_identity(sender),
    )


# -- TRU-001 Security advisory lifecycle --------------------------------------------------

TRU_001_PUBLISHED_SUBJECT = "Zoiko Steam security advisory {reference}"
TRU_001_UPDATED_SUBJECT = "Update to Zoiko Steam security advisory {reference}"
TRU_001_REMEDIATION_SUBJECT = ("Remediation available for Zoiko Steam security advisory "
                               "{reference}")
TRU_001_CLOSED_SUBJECT = "Zoiko Steam security advisory {reference} closed"

ADVISORY_VARIANT_SUBJECT = {
    "advisory_published": TRU_001_PUBLISHED_SUBJECT,
    "advisory_updated": TRU_001_UPDATED_SUBJECT,
    "advisory_remediation": TRU_001_REMEDIATION_SUBJECT,
    "advisory_closed": TRU_001_CLOSED_SUBJECT,
}

ADVISORY_SCOPE = ("You are receiving this because you are a verified security contact for "
                  "your organization. Security advisories are not marketing and cannot be "
                  "unsubscribed from; ask your organization owner to change your security "
                  "contacts.")


def _advisory_action_line(action_mandatory, deadline, remediation_steps):
    """The one imperative sentence, and only when policy earned it.

    `action_mandatory` is a recorded policy flag on the advisory. Without it the wording
    stays descriptive - no "upgrade immediately", no manufactured urgency - because a
    severity label is not a mandate and email code has no business deciding it is.
    """
    if not remediation_steps:
        return "No remediation is available yet. We will update this advisory when one is."
    if action_mandatory and deadline:
        return (f"This update is required. Please complete it by {deadline}.")
    if action_mandatory:
        return "This update is required. Please apply it as soon as you are able."
    return ("We recommend applying this update during your normal change process. No "
            "deadline has been set for this advisory.")


def _advisory_workaround_line(available, summary):
    """Truthful either way. "No workaround" is a real answer and is stated as one."""
    if available and summary:
        return summary
    return "No workaround is available."


def send_security_advisory_email(to, *, reference, variant, title, severity, summary,
                                 customer_impact, components, affected_versions,
                                 published_at, immediate_mitigation, fixed_version,
                                 remediation_steps, remediation_deadline,
                                 action_mandatory, workaround_available,
                                 workaround_summary, change_summary, changed_fields,
                                 closure_note, affected_customer, cvss_vector):
    """One advisory message, four variants.

    `severity` arrives as a recorded category and is rendered as a label - nothing here
    computes or upgrades it. `cvss_vector` is rendered ONLY when a human recorded a real
    one; there is no scoring in this platform, so it is normally absent and no score is
    implied. `affected_customer` says whether an authoritative AdvisoryImpact row maps the
    reader's organization to this advisory, so the message can say "this affects your
    organization" only when that is a recorded fact.
    """
    subject_template = ADVISORY_VARIANT_SUBJECT.get(variant, TRU_001_PUBLISHED_SUBJECT)
    subject = subject_template.format(reference=reference)
    severity_label = {"low": "Low", "medium": "Medium", "high": "High",
                      "critical": "Critical"}.get(severity, "Under assessment")

    rows = [("Advisory", reference), ("Severity", severity_label)]
    if cvss_vector:
        rows.append(("CVSS vector", cvss_vector))
    rows.append(("Affected components", _bullets(components) if components
                 else "See the advisory"))
    # Only stated when somebody recorded it. "All versions" is never assumed.
    rows.append(("Affected versions", affected_versions or "Stated in the advisory"))
    if published_at:
        rows.append(("Published", billing_date(published_at)))
    rows.append(("Applies to your organization",
                 "Yes - our records show your organization is affected"
                 if affected_customer else
                 "Not determined - review the advisory against your configuration"))

    if variant == "advisory_published":
        headline = f"Security advisory {reference}."
        body = summary
        if customer_impact:
            rows.append(("What this means for you", customer_impact))
        rows.append(("Immediate mitigation",
                     immediate_mitigation or "No interim mitigation is available yet."))
        rows.append(("Workaround", _advisory_workaround_line(workaround_available,
                                                             workaround_summary)))
        footer = ADVISORY_SCOPE
    elif variant == "advisory_updated":
        headline = f"Advisory {reference} has been updated."
        # The update shows the CHANGE. The previous statement is not overwritten - it stays
        # in the advisory's published version history in the Trust Center.
        body = change_summary or "This advisory has been updated."
        if changed_fields:
            rows.append(("What changed", _bullets(changed_fields)))
        rows.append(("Current summary", summary))
        if customer_impact:
            rows.append(("What this means for you", customer_impact))
        rows.append(("Previous versions",
                     "Earlier versions of this advisory remain published in the Trust "
                     "Center."))
        footer = ADVISORY_SCOPE
    elif variant == "advisory_remediation":
        headline = f"Remediation is available for advisory {reference}."
        body = ("A fix for this advisory is now available. " + (summary or ""))
        rows.append(("Fixed version", fixed_version or "See remediation steps"))
        rows.append(("Remediation steps", remediation_steps or "See the advisory"))
        rows.append(("Workaround", _advisory_workaround_line(workaround_available,
                                                             workaround_summary)))
        # A deadline appears only when a real one was recorded on the advisory.
        rows.append(("Deadline", billing_date(remediation_deadline)
                     if remediation_deadline else "No deadline has been set"))
        rows.append(("Required action", _advisory_action_line(
            action_mandatory, billing_date(remediation_deadline)
            if remediation_deadline else None, remediation_steps)))
        footer = ADVISORY_SCOPE
    else:
        headline = f"Advisory {reference} is closed."
        body = closure_note or "This advisory is closed."
        # Closure describes the ADVISORY, not the customer estate. Saying otherwise would
        # tell an organization it had patched when nothing here knows that.
        rows.append(("What closure means",
                     "This advisory is closed on our side. It does not confirm that the "
                     "update has been applied in your organization - please verify against "
                     "your own records."))
        rows.append(("Fixed version", fixed_version or "See the advisory"))
        footer = ADVISORY_SCOPE

    return _trust_send(to, subject, "Security advisory", headline,
                       f"Zoiko Steam security advisory {reference}.", rows, body,
                       "View in the Trust Center", trust_center_url(), footer,
                       sender=SENDER_SECURITY_ADVISORY)


# -- TRU-002 Trust evidence request and access --------------------------------------------

TRU_002_RECEIVED_SUBJECT = "We received your Zoiko Steam Trust Center request"
TRU_002_APPROVED_SUBJECT = "Your Zoiko Steam Trust Center document is available"
TRU_002_DENIED_SUBJECT = "About your Zoiko Steam Trust Center request"
TRU_002_EXPIRED_SUBJECT = "Your Zoiko Steam Trust Center access expired"
TRU_002_REVOKED_SUBJECT = "Your Zoiko Steam Trust Center access was withdrawn"

EVIDENCE_DECISION_SUBJECT = {
    "denied": TRU_002_DENIED_SUBJECT,
    "expired": TRU_002_EXPIRED_SUBJECT,
    "revoked": TRU_002_REVOKED_SUBJECT,
}

PURPOSE_LABELS = {
    "vendor_security_review": "Vendor security review",
    "procurement_due_diligence": "Procurement due diligence",
    "customer_audit": "Customer audit",
    "regulatory_compliance": "Regulatory compliance",
    "contract_negotiation": "Contract negotiation",
}

SCOPE_LABELS = {
    "organization": "Organization-wide",
    "single_project": "A single project",
    "annual_review": "Annual review",
}

DOCUMENT_TYPE_LABELS = {
    "soc2_type2": "SOC 2 Type II report",
    "iso27001_certificate": "ISO 27001 certificate",
    "penetration_test_summary": "Penetration test summary",
    "security_whitepaper": "Security whitepaper",
    "subprocessor_list": "Subprocessor list",
    "dpa_template": "Data processing agreement",
    "architecture_overview": "Architecture overview",
    "questionnaire_response": "Security questionnaire response",
}


def send_trust_request_received_email(to, *, reference, requester_name, document_title,
                                      document_type, purpose, scope, status):
    """Acknowledge a request. Carries no document and no link to one."""
    body = ("We have received your Trust Center request and it is with our team for "
            "review. We will email you when a decision has been made.")
    rows = [("Request", reference),
            ("Document", document_title or DOCUMENT_TYPE_LABELS.get(document_type,
                                                                    "Requested document")),
            ("Purpose", PURPOSE_LABELS.get(purpose, purpose)),
            ("Scope", SCOPE_LABELS.get(scope, scope)),
            ("Status", "Under review" if status == "under_review" else "Received")]
    headline = "We received your Trust Center request."
    return _trust_send(to, TRU_002_RECEIVED_SUBJECT, "Trust Center", headline,
                       "Your Trust Center request has been received.", rows, body,
                       "Visit the Trust Center", trust_center_url(),
                       "Confidential documents are released under an approved request only.")


def send_trust_access_approved_email(to, *, reference, requester_name, document_title,
                                     document_version, classification, purpose, scope,
                                     expires_at, url):
    """Send the short-lived, bound access link.

    Note what is NOT a parameter: the document, its contents, its storage key, or any
    attachment. There is no way for the evidence itself to travel in this message - the
    recipient authenticates against a bound, expiring authorization and the file streams
    from private storage.
    """
    body = ("Your Trust Center request has been approved. Use the link below to access the "
            "document. The link is issued to this address for the purpose and scope you "
            "requested, and it expires.")
    rows = [("Request", reference),
            ("Document", document_title or "Approved document"),
            ("Version", document_version or "Current"),
            ("Classification", {"public": "Public",
                                "customer_confidential": "Customer confidential",
                                "nda_required": "Confidential - NDA required"}.get(
                                    classification, "Confidential")),
            ("Purpose", PURPOSE_LABELS.get(purpose, purpose)),
            ("Scope", SCOPE_LABELS.get(scope, scope)),
            ("Access expires", billing_date(expires_at) if expires_at else "Not set")]
    return _trust_send(to, TRU_002_APPROVED_SUBJECT, "Trust Center",
                       "Your document is available.",
                       "Your Trust Center document is ready.", rows, body,
                       "Access the document", url,
                       "This link is issued to you for this document and expires. Please do "
                       "not forward it - a forwarded link will not work for anyone else.")


def send_trust_request_decided_email(to, *, reference, requester_name, variant,
                                     decision_note):
    """Denial, expiry and revocation. Carries a customer-safe reason and nothing internal."""
    subject = EVIDENCE_DECISION_SUBJECT.get(variant, TRU_002_DENIED_SUBJECT)
    if variant == "denied":
        headline = "We could not approve this request."
        body = ("We were not able to approve your Trust Center request. " +
                (decision_note or ""))
        rows = [("Request", reference), ("Outcome", "Not approved")]
    elif variant == "expired":
        headline = "Your access has expired."
        body = ("The access window for your Trust Center document has ended and the link no "
                "longer works. You are welcome to request access again.")
        rows = [("Request", reference), ("Outcome", "Access expired")]
    else:
        headline = "Your access has been withdrawn."
        body = ("Access to your Trust Center document has been withdrawn and the link no "
                "longer works. " + (decision_note or ""))
        rows = [("Request", reference), ("Outcome", "Access withdrawn")]
    return _trust_send(to, subject, "Trust Center", headline,
                       "An update on your Trust Center request.", rows, body,
                       "Visit the Trust Center", trust_center_url(),
                       "Requests are reviewed individually and access is time-limited.")


# -- TRU-003 Vulnerability disclosure -----------------------------------------------------

TRU_003_RECEIVED_SUBJECT = "We received your Zoiko Steam security report"
TRU_003_UPDATE_SUBJECT = "Update on your Zoiko Steam security report {reference}"

# What acknowledgement is allowed to say, and what it deliberately does not.
#
# NO bounty. NO payout. NO public credit. NO validity judgement. NO remediation deadline.
# None of those exist as policy in this platform, and a researcher acting on an implied
# promise would be reasonable to feel misled. So the copy commits to exactly one thing:
# somebody will look at it.
VULN_NO_PROMISE = ("We do not operate a paid bug bounty programme, and this message is not "
                   "an assessment of the report's validity. We will tell you what we find.")

VULN_SAFE_HANDLING = ("Please keep the details of your report confidential while we "
                      "investigate, and avoid accessing, changing or storing other people's "
                      "data. Do not send us passwords, API keys or private keys - we never "
                      "need them to reproduce an issue.")

VULN_STAGE_HEADLINE = {
    "acknowledged": "We have your report.",
    "clarification_needed": "We need a little more information.",
    "coordinating": "We are working on a fix.",
    "remediated": "The issue you reported has been fixed.",
    "closed": "We have closed your report.",
}


def send_vulnerability_received_email(to, *, reference, reporter_name, received_at,
                                      category, portal_url):
    """Acknowledge a researcher's report through the protected channel.

    `portal_url` is the researcher's own bound handle on their own report - it grants read of
    the safe status view and nothing else anywhere in the platform.
    """
    body = ("Thank you for reporting this to us. Your report is with our security team. " +
            VULN_NO_PROMISE)
    rows = [("Report", reference),
            ("Received", billing_date(received_at) if received_at else "Just now"),
            ("Category", (category or "").replace("_", " ").title()),
            ("What happens next",
             "Our security team reviews every report. We will contact you if we need more "
             "information, and we will tell you the outcome."),
            ("Safe handling", VULN_SAFE_HANDLING)]
    return _trust_send(to, TRU_003_RECEIVED_SUBJECT, "Security report",
                       "We received your security report.",
                       "Your report is with the Zoiko Steam security team.", rows, body,
                       "Track your report", portal_url,
                       "Use the secure link above to follow your report or send us more "
                       "information. Please do not include credentials.",
                       sender=SENDER_SECURITY_ADVISORY)


def send_vulnerability_update_email(to, *, reference, reporter_name, stage, body,
                                    resolution, coordination_recorded, portal_hint):
    """One researcher-safe lifecycle update.

    `body` is the stored researcher-safe text. There is no parameter for internal analysis,
    detection logic, other customers, or an exploit - and `coordination_recorded` gates the
    only place a disclosure timeline could be mentioned, so with no coordination policy
    configured nothing about embargoes or dates is ever said.
    """
    subject = TRU_003_UPDATE_SUBJECT.format(reference=reference)
    headline = VULN_STAGE_HEADLINE.get(stage, "An update on your report.")
    rows = [("Report", reference), ("Stage", (stage or "").replace("_", " ").title())]
    if stage == "closed" and resolution:
        rows.append(("Outcome", (resolution or "").replace("_", " ").title()))
    if stage == "coordinating":
        rows.append(("Disclosure timeline",
                     "We will agree any disclosure timing with you directly."
                     if coordination_recorded else
                     "We have not set a disclosure timeline for this report."))
    if stage == "remediated":
        rows.append(("Next step",
                     "If you can, please confirm the fix resolves what you reported."))
    return _trust_send(to, subject, "Security report", headline,
                       "An update on your Zoiko Steam security report.", rows, body,
                       "View your report", trust_center_url(),
                       VULN_SAFE_HANDLING, sender=SENDER_SECURITY_ADVISORY)


# ═════════════════════════════════════════════════════════════════════════════════════════
# ZST-EC-001 MKT-001 -> MKT-004 — Marketing and product education
# ═════════════════════════════════════════════════════════════════════════════════════════
# MARKETING class, and separated from everything above by construction:
#
#   every promotional sender below REQUIRES an `unsubscribe_url`, and
#   no security, identity, privacy, billing or support template accepts one.
#
# So a marketing message always carries one-click unsubscribe, and unsubscribing can only
# reach messages that carry it. The two properties are the same guarantee seen from either
# end, and the test suite asserts both by inspecting the signatures.

SENDER_MARKETING = "Zoiko Steam"

# On every promotional message. States the basis, not just the mechanism: somebody who does
# not remember opting in should be able to tell what they are looking at.
MKT_SCOPE = ("You are receiving this because you subscribed to Zoiko Steam product updates. "
             "This is separate from your account, security, billing and privacy emails, "
             "which are not affected if you unsubscribe.")


def preference_center_url() -> str:
    return f"{public_base_url()}/preferences"


def _mkt_send(to, subject, header, headline, preheader, rows, body, cta, url,
              *, unsubscribe_url, manage_url=None):
    """Every marketing send goes through here, and `unsubscribe_url` is keyword-REQUIRED.

    A promotional message without a working one-click unsubscribe cannot be constructed.
    """
    footer = MKT_SCOPE + f" Unsubscribe: {unsubscribe_url}"
    if manage_url:
        footer += f" Manage what you receive: {manage_url}"
    return _send(
        to, subject,
        _org_shell(header, headline, preheader, rows, html.escape(body), cta, url, footer),
        _org_text(headline, preheader, rows, body, cta, url, footer, SENDER_MARKETING),
        sender=_sender_identity(SENDER_MARKETING),
    )


# -- MKT foundation: consent lifecycle ----------------------------------------------------

MKT_VERIFY_SUBJECT = "Confirm your Zoiko Steam product updates"
MKT_CONFIRMED_SUBJECT = "You're subscribed to Zoiko Steam product updates"
MKT_PREFERENCES_SUBJECT = "Your Zoiko Steam email preferences changed"
MKT_UNSUBSCRIBED_SUBJECT = "You unsubscribed from Zoiko Steam product updates"


def send_marketing_verify_email(to, *, topics, expires_at, confirm_url):
    """Double opt-in confirmation. Transactional - it is the consent request itself, so it
    carries no unsubscribe link because there is nothing yet to unsubscribe from."""
    body = ("Please confirm this address so we can send you the Zoiko Steam updates you "
            "asked for. If you did not request this, ignore this email and nothing will be "
            "sent.")
    rows = [("You asked for", _bullets(topics)),
            ("Link expires", billing_date(expires_at) if expires_at else "Not set")]
    return _send(
        to, MKT_VERIFY_SUBJECT,
        _org_shell("Product updates", "Confirm your subscription.",
                   "Confirm your Zoiko Steam product updates.", rows, html.escape(body),
                   "Confirm subscription", confirm_url,
                   "You will not receive anything until you confirm."),
        _org_text("Confirm your subscription.",
                  "Confirm your Zoiko Steam product updates.", rows, body,
                  "Confirm subscription", confirm_url,
                  "You will not receive anything until you confirm.", SENDER_MARKETING),
        sender=_sender_identity(SENDER_MARKETING),
    )


def send_marketing_confirmed_email(to, *, topics, manage_url, unsubscribe_url):
    body = "Thanks - you're subscribed to the Zoiko Steam updates you chose."
    rows = [("You will receive", _bullets(topics)),
            ("Not affected",
             "Account, security, billing and privacy emails are separate and are never "
             "affected by this subscription.")]
    return _mkt_send(to, MKT_CONFIRMED_SUBJECT, "Product updates",
                     "You're subscribed.", "Your Zoiko Steam subscription is active.",
                     rows, body, "Manage what you receive", manage_url,
                     unsubscribe_url=unsubscribe_url, manage_url=manage_url)


def send_marketing_preferences_email(to, *, previous_topics, current_topics, manage_url,
                                     unsubscribe_url):
    body = "Your Zoiko Steam email preferences have been updated."
    rows = [("Previously", _bullets(previous_topics) if previous_topics else "Nothing"),
            ("Now", _bullets(current_topics) if current_topics else "Nothing")]
    return _mkt_send(to, MKT_PREFERENCES_SUBJECT, "Product updates",
                     "Your preferences changed.",
                     "Your Zoiko Steam email preferences changed.", rows, body,
                     "Manage what you receive", manage_url,
                     unsubscribe_url=unsubscribe_url, manage_url=manage_url)


def send_marketing_unsubscribed_email(to, *, resubscribe_url):
    """Confirms suppression, and states plainly what was NOT switched off.

    Transactional - it is the receipt for an action, and offering to unsubscribe from an
    unsubscribe confirmation would be absurd.
    """
    body = ("You have been unsubscribed from Zoiko Steam product updates. This takes effect "
            "immediately.")
    rows = [("Product updates", "Stopped"),
            ("Still active",
             "Account, security, billing, privacy and support emails are separate and are "
             "unaffected. If you subscribe to the status page, that is also unaffected.")]
    return _send(
        to, MKT_UNSUBSCRIBED_SUBJECT,
        _org_shell("Product updates", "You have unsubscribed.",
                   "You unsubscribed from Zoiko Steam product updates.", rows,
                   html.escape(body), "Subscribe again", resubscribe_url,
                   "Security, billing and privacy emails are not affected."),
        _org_text("You have unsubscribed.",
                  "You unsubscribed from Zoiko Steam product updates.", rows, body,
                  "Subscribe again", resubscribe_url,
                  "Security, billing and privacy emails are not affected.",
                  SENDER_MARKETING),
        sender=_sender_identity(SENDER_MARKETING),
    )


# -- MKT-001 Release notes digest ---------------------------------------------------------

MKT_001_SUBJECT = "What's new in Zoiko Steam"


def send_release_digest_email(to, *, title, summary, entries, period_start, period_end,
                              manage_url, unsubscribe_url):
    """An approved digest of approved releases.

    `entries` carry each release's APPROVED customer summary. There is no parameter for
    `Release.notes` - the internal changelog prose has no route into this message.
    """
    body = summary or "Here's what we shipped recently."
    rows = [("Period", f"{billing_date(period_start)} to {billing_date(period_end)}")]
    for entry in entries or []:
        label = f"{entry.get('version') or 'Release'} - {entry.get('title') or ''}".strip(" -")
        value = entry.get("summary") or ""
        if entry.get("rollout_status"):
            value += f" (Rollout: {entry['rollout_status']})"
        if entry.get("documentation_path"):
            value += f" Documentation: {public_base_url()}{entry['documentation_path']}"
        rows.append((label, value))
    return _mkt_send(to, MKT_001_SUBJECT, "Product updates", title or "What's new.",
                     "What's new in Zoiko Steam.", rows, body,
                     "Read the release notes", f"{public_base_url()}/releases",
                     unsubscribe_url=unsubscribe_url, manage_url=manage_url)


# -- MKT-002 Feature availability announcement --------------------------------------------

# Availability wording is chosen by the SERVICE from the frozen lifecycle
# (marketing.announcement_subject), never composed here. This sender receives the subject it
# must use, so there is no code path in which a preview is described as generally available.

AVAILABILITY_NOTE = {
    "preview": ("This feature is in preview. Preview features are still changing, are not "
                "covered by the standard service commitments, and may be withdrawn."),
    "pilot": ("This is a pilot. Pilot access is limited and may change or end while we "
              "learn from it."),
    "beta": ("This feature is in beta. It is more stable than preview but is still "
             "changing."),
    "regional": ("This feature is rolling out by region. It is available to your "
                 "organization; it may not be available everywhere yet."),
    "restricted": ("This feature has limited availability and is enabled for selected "
                   "organizations."),
    "invite_only": ("This feature is available by invitation. Your organization has been "
                    "given access."),
    "ga": "This feature is generally available.",
}


def send_feature_announcement_email(to, *, subject, feature_name, lifecycle,
                                    lifecycle_label, headline, body,
                                    documentation_path, effective_at, manage_url,
                                    unsubscribe_url):
    rows = [("Feature", feature_name), ("Availability", lifecycle_label),
            ("What that means", AVAILABILITY_NOTE.get(lifecycle,
                                                      "Availability is limited."))]
    if effective_at:
        rows.append(("Available from", billing_date(effective_at)))
    if documentation_path:
        rows.append(("Documentation", f"{public_base_url()}{documentation_path}"))
    return _mkt_send(to, subject, "Product updates",
                     headline or f"{feature_name}: {lifecycle_label}.",
                     f"{feature_name} - {lifecycle_label}.", rows, body,
                     "See what's new", f"{public_base_url()}/releases",
                     unsubscribe_url=unsubscribe_url, manage_url=manage_url)


# -- MKT-003 Developer onboarding series --------------------------------------------------

MKT_003_SUBJECT = {
    "welcome": "Getting started with the Zoiko Steam API",
    "build": "Building your Zoiko Steam integration",
    "production_readiness": "Taking your Zoiko Steam integration to production",
}

# Each step's copy is tied to the milestone that unlocked it, so the message describes
# something the reader actually did. There is deliberately no step whose copy claims to have
# seen API traffic: that milestone is not observable here (no API-key request authentication,
# no per-key telemetry), so it is not used and not implied.
MKT_003_BODY = {
    "welcome": ("Welcome. Here's the short path to your first Zoiko Steam API call: create "
                "a credential in your organization's developer settings, read the quickstart, "
                "and try a request against a test event."),
    "build": ("You've created a credential - nice. Next up: webhooks. Register an endpoint, "
              "verify it, and check the signature on every delivery so you can trust what "
              "you receive."),
    "production_readiness": ("Your webhook endpoint is verified, which means you're close to "
                             "production. Before you go live: handle retries idempotently, "
                             "rotate your signing secret on a schedule, and know your rate "
                             "limits."),
}

MKT_003_STEP_ROW = {
    "welcome": ("Where you are", "You subscribed to developer education."),
    "build": ("Where you are", "You have created an API credential."),
    "production_readiness": ("Where you are", "You have a verified webhook endpoint."),
}


def send_developer_onboarding_email(to, *, step, milestone, manage_url, unsubscribe_url):
    subject = MKT_003_SUBJECT.get(step, MKT_003_SUBJECT["welcome"])
    body = MKT_003_BODY.get(step, MKT_003_BODY["welcome"])
    rows = [MKT_003_STEP_ROW.get(step, MKT_003_STEP_ROW["welcome"]),
            ("Part of", "Developer education - a short series, not a drip campaign.")]
    return _mkt_send(to, subject, "Developer education",
                     subject + ".", "Developer education from Zoiko Steam.", rows, body,
                     "Open the developer docs", f"{public_base_url()}/developers",
                     unsubscribe_url=unsubscribe_url, manage_url=manage_url)


# -- MKT-004 Guides and webinars ----------------------------------------------------------

MKT_004_GUIDE_SUBJECT = "Your Zoiko Steam guide: {guide}"
MKT_004_CONFIRMATION_SUBJECT = "You're registered: {title}"
MKT_004_REMINDER_SUBJECT = "Starting soon: {title}"
MKT_004_RESCHEDULED_SUBJECT = "New time for {title}"
MKT_004_CANCELED_SUBJECT = "Cancelled: {title}"
MKT_004_FOLLOWUP_SUBJECT = "After {title}"

WEBINAR_VARIANT_SUBJECT = {
    "confirmation": MKT_004_CONFIRMATION_SUBJECT,
    "reminder": MKT_004_REMINDER_SUBJECT,
    "rescheduled": MKT_004_RESCHEDULED_SUBJECT,
    "cancelled": MKT_004_CANCELED_SUBJECT,
}

WEBINAR_VARIANT_HEADLINE = {
    "confirmation": "You're registered.",
    "reminder": "Starting soon.",
    "rescheduled": "This session has moved.",
    "cancelled": "This session has been cancelled.",
}

# Registering, reminding, rescheduling and cancelling are TRANSACTIONAL to somebody who
# signed up: they are about the thing that person asked for. So they do not carry a
# marketing unsubscribe, and they are not gated on marketing consent - cancelling a session
# somebody planned their day around must not depend on their campaign preferences.
WEBINAR_SCOPE = ("You are receiving this because you registered for this session. "
                 "Registering does not subscribe you to marketing.")


def send_guide_email(to, *, name, guide, guide_label, subscribed):
    """Fulfil a guide request. Transactional: they asked for this specific document.

    `subscribed` reflects whether they ALSO opted in on the form. When they did not, the
    message says so explicitly rather than leaving them guessing.
    """
    subject = MKT_004_GUIDE_SUBJECT.format(guide=guide_label)
    body = (f"Here is the guide you asked for: {guide_label}.")
    rows = [("Guide", guide_label),
            ("Your subscription",
             "You also asked for Live Events education emails - please confirm your address "
             "using the separate email we just sent." if subscribed else
             "You are not subscribed to any marketing emails. We sent this because you "
             "asked for this guide.")]
    return _send(
        to, subject,
        _org_shell("Live Events education", f"Your guide: {guide_label}.",
                   f"Your Zoiko Steam guide: {guide_label}.", rows, html.escape(body),
                   "Read the guide", f"{public_base_url()}/guides/{guide}",
                   "You asked for this guide. We have not subscribed you to anything else."),
        _org_text(f"Your guide: {guide_label}.",
                  f"Your Zoiko Steam guide: {guide_label}.", rows, body,
                  "Read the guide", f"{public_base_url()}/guides/{guide}",
                  "You asked for this guide. We have not subscribed you to anything else.",
                  SENDER_MARKETING),
        sender=_sender_identity(SENDER_MARKETING),
    )


def send_webinar_email(to, *, name, variant, reference, title, description,
                       starts_at_utc, previous_starts_at_utc, duration_minutes,
                       join_path):
    """Registration lifecycle. Transactional to a registrant, so no marketing gate."""
    subject = WEBINAR_VARIANT_SUBJECT.get(variant, MKT_004_CONFIRMATION_SUBJECT).format(
        title=title)
    headline = WEBINAR_VARIANT_HEADLINE.get(variant, "About your session.")
    rows = [("Session", title), ("Reference", reference)]
    if variant == "rescheduled":
        # The old time stays visible, so nobody has to trust their memory.
        rows.append(("Previous time (UTC)", billing_date(previous_starts_at_utc)
                     if previous_starts_at_utc else "Not recorded"))
        rows.append(("New time (UTC)", billing_date(starts_at_utc)))
    elif variant != "cancelled":
        rows.append(("Time (UTC)", billing_date(starts_at_utc)))
        rows.append(("Duration", f"{duration_minutes} minutes"))
    if description and variant in ("confirmation", "reminder"):
        rows.append(("What we'll cover", description))
    if variant == "cancelled":
        rows.append(("What happens now",
                     "Nothing is required from you. We will let you know if we run this "
                     "session again."))
    cta_url = (f"{public_base_url()}{join_path}" if join_path
               else f"{public_base_url()}/webinars")
    cta = "Cancel your registration" if variant == "cancelled" else "Join the session"
    if variant == "cancelled":
        cta, cta_url = "See upcoming sessions", f"{public_base_url()}/webinars"
    return _send(
        to, subject,
        _org_shell("Live Events education", headline, f"{title} - {headline}", rows,
                   html.escape(description or title), cta, cta_url, WEBINAR_SCOPE),
        _org_text(headline, f"{title} - {headline}", rows, description or title, cta,
                  cta_url, WEBINAR_SCOPE, SENDER_MARKETING),
        sender=_sender_identity(SENDER_MARKETING),
    )


def send_webinar_followup_email(to, *, name, title, body, manage_url, unsubscribe_url):
    """The promotional follow-up. Requires approval AND the recipient's own consent.

    Note the required `unsubscribe_url`: unlike the registration lifecycle above, this is
    marketing, and it is classified and constructed as marketing.
    """
    subject = MKT_004_FOLLOWUP_SUBJECT.format(title=title)
    rows = [("Session", title),
            ("Why you're receiving this",
             "You subscribed to Live Events education. Attending a session does not "
             "subscribe you to anything.")]
    return _mkt_send(to, subject, "Live Events education", f"After {title}.",
                     f"Following up on {title}.", rows, body,
                     "See upcoming sessions", f"{public_base_url()}/webinars",
                     unsubscribe_url=unsubscribe_url, manage_url=manage_url)
