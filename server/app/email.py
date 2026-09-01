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
