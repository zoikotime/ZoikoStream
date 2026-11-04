"""Google sign-in (OpenID Connect, authorization-code flow with PKCE).

Shape of the flow — chosen to fit the platform's EXISTING session model, which is a bearer
token returned in JSON and kept by the SPA (no auth cookies), with the SPA and the API on
different origins:

  1. POST /api/auth/google/start
       The API mints a one-time nonce and a PKCE code_verifier and returns
         - authorization_url : Google's consent page (client_id, redirect_uri, scope, nonce,
                               S256 code_challenge, and `state`)
         - state             : a short-lived token SIGNED with SECRET_KEY that carries the
                               nonce and a hash of the verifier
         - code_verifier     : kept by the SPA in sessionStorage — never placed in a URL
  2. Google redirects the browser to GOOGLE_REDIRECT_URI, a route in the SPA, with
     ?code&state. The SPA compares `state` with the one it stored for this tab (login-CSRF:
     an attacker cannot plant sessionStorage in the victim's tab) and then calls
  3. POST /api/auth/google/callback {code, state, code_verifier}
       The API verifies the state's signature, purpose and expiry, checks the verifier
       matches the hash inside it, exchanges the code with Google server-to-server
       (client_secret + PKCE), and verifies the returned ID token: RS256 signature against
       Google's published keys, issuer, audience == GOOGLE_CLIENT_ID, iat/exp, the nonce
       from step 1, email_verified, and that Google is AUTHORITATIVE for the email's domain
       (see google_is_authoritative below). Only then is an identity returned.

No token is ever put in a URL, and no new session system exists: the router hands the
verified identity to the same account checks and the same create_access_token() that
password sign-in uses.

IDENTITY, NOT EMAIL. The router signs a Google identity in ONLY through a stored link to its
`sub` (models/user_identity.py). Links are made by the signed-in account holder after a
password step-up, using the same flow with a separate state purpose (STATE_PURPOSE_LINK)
bound to that user and that session, so a link state can never complete a sign-in and a
sign-in state can never create a link.

FAIL CLOSED. With any of GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI unset,
`is_configured()` is False and every entry point raises GoogleOAuthError(NOT_CONFIGURED). No
check is ever skipped or relaxed because configuration is missing.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from google.auth import exceptions as google_auth_exceptions
from google.auth import jwt as google_jwt
from jose import JWTError, jwt as jose_jwt

from ..config import settings
from ..schemas.auth import PKCE_CODE_VERIFIER_PATTERN

log = logging.getLogger(__name__)

# fullmatch, so a trailing newline can never slip past `$`.
_CODE_VERIFIER_RE = re.compile(PKCE_CODE_VERIFIER_PATTERN)

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# PEM certificates keyed by `kid` — the format google.auth.jwt.decode() consumes.
CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
VALID_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
SCOPES = "openid email profile"

STATE_PURPOSE = "google_oauth_state"          # sign-in
STATE_PURPOSE_LINK = "google_oauth_link"      # linking Google to the signed-in account
STATE_TTL = timedelta(minutes=10)

# The one consumer-mail domain Google is authoritative for. Google's OpenID Connect guidance:
# `email_verified` proves current ownership only for @gmail.com addresses and for Google
# Workspace accounts (`hd` present). For any other domain it only says the address was
# verified once, when the Google account was created — a former employee can keep a Google
# account on an address they no longer control.
GMAIL_DOMAIN = "gmail.com"
STATE_ALGORITHM = "HS256"
# Tolerated clock difference when checking the ID token's iat/exp.
CLOCK_SKEW_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 10.0

# Error codes. Stable strings: the SPA branches on them.
NOT_CONFIGURED = "GOOGLE_SIGN_IN_UNAVAILABLE"
INVALID_STATE = "GOOGLE_STATE_INVALID"
EXCHANGE_FAILED = "GOOGLE_EXCHANGE_FAILED"
INVALID_TOKEN = "GOOGLE_TOKEN_INVALID"
EMAIL_UNVERIFIED = "GOOGLE_EMAIL_UNVERIFIED"
DOMAIN_UNVERIFIED = "GOOGLE_DOMAIN_UNVERIFIED"


# Google's endpoints are called through these two seams rather than httpx.post/get directly:
# httpx's module functions are shared process-wide (the email module uses httpx.post too), so
# replacing them in a test would also intercept unrelated traffic.
def _post(url, **kwargs):
    return httpx.post(url, **kwargs)


def _get(url, **kwargs):
    return httpx.get(url, **kwargs)


class GoogleOAuthError(Exception):
    """A refused Google sign-in. `code` is stable; `message` is safe to show a user —
    it never echoes token contents, Google's raw error body or configuration values."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str            # Google's stable account id
    email: str          # lower-cased; Google has asserted it is verified
    name: str | None
    hosted_domain: str | None  # `hd` — present for Google Workspace accounts


@dataclass(frozen=True)
class AuthorizationRequest:
    authorization_url: str
    state: str
    code_verifier: str


# ── configuration ────────────────────────────────────────────────────────────────────────

def is_configured() -> bool:
    return all(
        (v or "").strip()
        for v in (settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET, settings.GOOGLE_REDIRECT_URI)
    )


def _require_configured() -> None:
    if not is_configured():
        raise GoogleOAuthError(NOT_CONFIGURED, "Google sign-in isn't available yet. Please use your work email and password.")


# ── step 1: authorization request ───────────────────────────────────────────────────────

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _verifier_hash(code_verifier: str) -> str:
    return hashlib.sha256(code_verifier.encode("ascii")).hexdigest()


def build_authorization_request(*, now: datetime | None = None, purpose: str = STATE_PURPOSE,
                                user_id: str | None = None,
                                session_id: str | None = None) -> AuthorizationRequest:
    """`purpose` is STATE_PURPOSE (sign-in) or STATE_PURPOSE_LINK. A link state also carries
    the account (`uid`) and the server-side session (`sid`) that asked for it, so it can only
    be completed by that same signed-in session."""
    _require_configured()
    if purpose not in (STATE_PURPOSE, STATE_PURPOSE_LINK):
        raise ValueError(f"unknown Google state purpose {purpose!r}")
    if purpose == STATE_PURPOSE_LINK and not (user_id and session_id):
        raise ValueError("a link state must name the account and the session")
    now = now or datetime.now(timezone.utc)
    nonce = secrets.token_urlsafe(32)
    # RFC 7636: 43–128 chars from the unreserved set. token_urlsafe(64) gives 86.
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())

    claims = {
        "purpose": purpose,
        "nonce": nonce,
        "cvh": _verifier_hash(code_verifier),
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + STATE_TTL).timestamp()),
    }
    if purpose == STATE_PURPOSE_LINK:
        claims.update(uid=str(user_id), sid=str(session_id))
    state = jose_jwt.encode(claims, settings.SECRET_KEY, algorithm=STATE_ALGORITHM)

    query = urlencode({
        "client_id": settings.GOOGLE_CLIENT_ID.strip(),
        "redirect_uri": settings.GOOGLE_REDIRECT_URI.strip(),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    })
    return AuthorizationRequest(f"{AUTHORIZATION_ENDPOINT}?{query}", state, code_verifier)


# ── step 3: callback ─────────────────────────────────────────────────────────────────────

def _decode_state(state: str, code_verifier: str, *, purpose: str = STATE_PURPOSE,
                  user_id: str | None = None, session_id: str | None = None) -> str:
    """Verify the state we signed in step 1 for THIS purpose and return its nonce. A link
    state must also name the calling account and session."""
    refused = GoogleOAuthError(INVALID_STATE, "This Google sign-in expired or was not started here. Please try again.")
    # The request schemas already enforce RFC 7636; this is the same rule again, checked before
    # any decoding or hashing, so no caller can reach `.encode("ascii")` with a value that
    # would raise instead of being refused.
    if not isinstance(code_verifier, str) or not _CODE_VERIFIER_RE.fullmatch(code_verifier):
        raise refused
    try:
        claims = jose_jwt.decode(state, settings.SECRET_KEY, algorithms=[STATE_ALGORITHM])
    except JWTError as exc:   # bad signature, malformed, or expired
        raise refused from exc
    if claims.get("purpose") != purpose or not claims.get("nonce") or not claims.get("cvh"):
        raise refused
    if not code_verifier or not secrets.compare_digest(_verifier_hash(code_verifier), claims["cvh"]):
        raise refused
    if purpose == STATE_PURPOSE_LINK:
        if not (user_id and session_id):
            raise refused
        if not secrets.compare_digest(str(claims.get("uid", "")), str(user_id)) or \
                not secrets.compare_digest(str(claims.get("sid", "")), str(session_id)):
            raise refused
    return claims["nonce"]


def _exchange_code(code: str, code_verifier: str) -> str:
    """Authorization code -> ID token, server to server. Returns the raw ID token."""
    try:
        resp = _post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID.strip(),
                "client_secret": settings.GOOGLE_CLIENT_SECRET.strip(),
                "redirect_uri": settings.GOOGLE_REDIRECT_URI.strip(),
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        log.warning("Google token exchange unreachable: %s", type(exc).__name__)
        raise GoogleOAuthError(EXCHANGE_FAILED, "Couldn't reach Google to finish signing in. Please try again.") from exc

    if resp.status_code != 200:
        # Google's error code only (e.g. invalid_grant) — never the body, which can echo input.
        try:
            err = resp.json().get("error")
        except ValueError:
            err = None
        log.warning("Google token exchange refused: status=%s error=%s", resp.status_code, err)
        raise GoogleOAuthError(EXCHANGE_FAILED, "Google didn't accept this sign-in. Please try again.")

    try:
        id_token = resp.json().get("id_token")
    except ValueError:
        id_token = None
    if not id_token:
        raise GoogleOAuthError(EXCHANGE_FAILED, "Google didn't return a sign-in token. Please try again.")
    return id_token


# Google rotates its signing keys; the response's Cache-Control max-age says how long the
# current set is good for. One fetch per max-age window, shared by every request.
_certs_lock = threading.Lock()
_certs_cache: dict = {"certs": None, "expires": 0.0}


def _parse_max_age(cache_control: str | None) -> int:
    for part in (cache_control or "").split(","):
        name, _, value = part.strip().partition("=")
        if name.lower() == "max-age" and value.isdigit():
            return int(value)
    return 300


def _fetch_certs() -> dict:
    with _certs_lock:
        if _certs_cache["certs"] and time.monotonic() < _certs_cache["expires"]:
            return _certs_cache["certs"]
        try:
            resp = _get(CERTS_URL, timeout=HTTP_TIMEOUT_SECONDS)
            resp.raise_for_status()
            certs = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Google signing keys unavailable: %s", type(exc).__name__)
            raise GoogleOAuthError(EXCHANGE_FAILED, "Couldn't verify the Google sign-in right now. Please try again.") from exc
        if not isinstance(certs, dict) or not certs:
            raise GoogleOAuthError(EXCHANGE_FAILED, "Couldn't verify the Google sign-in right now. Please try again.")
        _certs_cache.update(certs=certs, expires=time.monotonic() + _parse_max_age(resp.headers.get("cache-control")))
        return certs


def verify_id_token(id_token: str, *, expected_nonce: str) -> GoogleIdentity:
    """Every check, explicitly. Raises GoogleOAuthError; never returns a partial identity."""
    _require_configured()
    client_id = settings.GOOGLE_CLIENT_ID.strip()
    try:
        # Signature (RS256 against Google's current keys, matched by `kid`), iat/exp with a
        # small skew, and aud == our client id.
        claims = google_jwt.decode(
            id_token,
            certs=_fetch_certs(),
            audience=client_id,
            clock_skew_in_seconds=CLOCK_SKEW_SECONDS,
        )
    except (ValueError, google_auth_exceptions.GoogleAuthError) as exc:
        log.info("Google ID token rejected: %s", exc.__class__.__name__)
        raise GoogleOAuthError(INVALID_TOKEN, "Google sign-in couldn't be verified. Please try again.") from exc

    # Defence in depth: re-assert what decode() covers, plus what it does not.
    if claims.get("iss") not in VALID_ISSUERS:
        raise GoogleOAuthError(INVALID_TOKEN, "Google sign-in couldn't be verified. Please try again.")
    if claims.get("aud") != client_id:
        raise GoogleOAuthError(INVALID_TOKEN, "Google sign-in couldn't be verified. Please try again.")
    if not claims.get("exp") or int(claims["exp"]) + CLOCK_SKEW_SECONDS < int(time.time()):
        raise GoogleOAuthError(INVALID_TOKEN, "Google sign-in couldn't be verified. Please try again.")
    if not expected_nonce or not secrets.compare_digest(str(claims.get("nonce", "")), expected_nonce):
        raise GoogleOAuthError(INVALID_TOKEN, "Google sign-in couldn't be verified. Please try again.")
    if not claims.get("sub"):
        raise GoogleOAuthError(INVALID_TOKEN, "Google sign-in couldn't be verified. Please try again.")

    email = (claims.get("email") or "").strip().lower()
    verified = claims.get("email_verified")
    if isinstance(verified, str):          # Google has historically sent "true" as a string
        verified = verified.lower() == "true"
    if not email or verified is not True:
        raise GoogleOAuthError(
            EMAIL_UNVERIFIED,
            "Your Google account's email address isn't verified. Verify it with Google, or sign in with your work email and password.",
        )

    hosted_domain = claims.get("hd")
    if not google_is_authoritative(email, hosted_domain):
        raise GoogleOAuthError(
            DOMAIN_UNVERIFIED,
            "Google sign-in is only available for Gmail addresses and Google Workspace accounts. "
            "Please sign in with your work email and password.",
        )

    return GoogleIdentity(sub=str(claims["sub"]), email=email, name=claims.get("name"),
                          hosted_domain=hosted_domain.strip().lower() if hosted_domain else None)


def google_is_authoritative(email: str, hosted_domain) -> bool:
    """Whether Google's `email_verified` actually proves CURRENT control of `email`.

      * A Google Workspace account carries `hd`, its hosted domain. Google is authoritative
        only when `hd` is present and equals the email's own domain. An email suffix alone
        is never taken as proof of Workspace hosting.
      * Without `hd`, Google is authoritative only for @gmail.com.
      * Anything else (an address on a domain Google does not host, a Workspace account whose
        address sits on a secondary or alias domain, a malformed `hd`) fails closed.

    No domain allowlist: this is a statement about who controls the mailbox, not about which
    customers may use Google sign-in.
    """
    local, at, domain = (email or "").strip().lower().rpartition("@")
    if not at or not local or not domain:
        return False
    if hosted_domain is None:
        return domain == GMAIL_DOMAIN
    if not isinstance(hosted_domain, str):
        return False
    hd = hosted_domain.strip().lower()
    return bool(hd) and hd == domain


def complete(*, code: str, state: str, code_verifier: str, purpose: str = STATE_PURPOSE,
             user_id: str | None = None, session_id: str | None = None) -> GoogleIdentity:
    """Step 3, end to end. The state is checked BEFORE Google is contacted."""
    _require_configured()
    nonce = _decode_state(state, code_verifier, purpose=purpose, user_id=user_id, session_id=session_id)
    if not code:
        raise GoogleOAuthError(EXCHANGE_FAILED, "Google didn't return a sign-in code. Please try again.")
    id_token = _exchange_code(code, code_verifier)
    return verify_id_token(id_token, expected_nonce=nonce)
