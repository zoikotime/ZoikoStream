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
