"""Google sign-in (OpenID Connect + PKCE): app/services/google_oauth.py + /api/auth/google/*.

The cryptography here is REAL. Each test run generates its own RSA key pair and signs genuine
RS256 ID tokens with it; the service is pointed at that key's public half in place of Google's
published certificates. So signature, issuer, audience, expiry and nonce checks are exercised
for real. Only Google's token endpoint (the code -> id_token exchange) is replaced by a stub,
and the stub asserts it received the client secret and the PKCE verifier.

    configuration      none / some GOOGLE_* set -> 503, nothing contacted
    domain authority   @gmail.com, or Workspace `hd` == email domain; everything else 403
    identity           sign-in ONLY through a stored link to Google's `sub` — never by email
    linking            signed in + password step-up + own email; state bound to user+session;
                       one Google account per account and vice versa; never relinks itself
    super_admin        refused on every Google entry point, server-side
    account gates      disabled / unverified / risk block exactly like password sign-in
    tenant isolation   a link signs in exactly its own account, role and organization
    nothing else       password sign-in unchanged (including for super_admin)

Run with pytest (TEST_DATABASE_URL required, see conftest.py).
"""
import base64
import hashlib
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import crypt as google_crypt
from google.auth import jwt as google_jwt
from jose import jwt as jose_jwt
from starlette.testclient import TestClient

import app.main as m
from app import email as email_mod
from app import ratelimit
from app.config import settings
from app.db import SessionLocal
from app.models import (
    AUTH_METHOD_GOOGLE, AuditLog, AuthSession, Organization, SignInEvent, StepUpGrant, User,
    UserIdentity,
)
from app.security import hash_password
from app.services import google_oauth
from app.services import identity_security as idsec

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
CLIENT_ID = "test-client-123.apps.googleusercontent.com"
CLIENT_SECRET = "test-secret-not-real"
REDIRECT_URI = "http://localhost:5173/auth/google/callback"
KID = "test-kid-1"


def _now():
    return datetime.now(timezone.utc)


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private_pem, public_pem


GOOGLE_PRIVATE, GOOGLE_PUBLIC = _keypair()       # stands in for Google's signing key
ATTACKER_PRIVATE, _ = _keypair()                 # a key Google never published


def _sign(claims, private_pem=GOOGLE_PRIVATE, kid=KID):
    signer = google_crypt.RSASigner.from_string(private_pem, key_id=kid)
    return google_jwt.encode(signer, claims).decode()


class _TokenResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = "{}"

    def json(self):
        return self._body


class World:
    """Two organizations. Org A holds Workspace-domain accounts (org admin, host, disabled,
    unverified), a Gmail account and a super_admin; org B holds one Workspace account. Every
    Workspace account sits on this run's own hosted domain, so tokens carry a matching `hd`."""

    def __init__(self):
        tag = uuid.uuid4().hex[:8]
        self.domain = f"ws-{tag}.example"            # the run's "Google Workspace" domain
        db = SessionLocal()
        try:
            org = Organization(name=f"Google Co {tag}", status="active", timezone="UTC")
            other = Organization(name=f"Other Co {tag}", status="active", timezone="UTC")
            db.add_all([org, other])
            db.flush()
            self.org_id, self.other_org_id = org.id, other.id
            self.email = f"admin-{tag}@{self.domain}"
            self.user_id = self._u(db, self.email, role="org_admin")
            self.host_email = f"host-{tag}@{self.domain}"
            self.host_id = self._u(db, self.host_email, role="host")
            self.disabled_email = f"off-{tag}@{self.domain}"
            self.disabled_id = self._u(db, self.disabled_email, is_active=False)
            self.unverified_email = f"new-{tag}@{self.domain}"
            self.unverified_id = self._u(db, self.unverified_email, email_verified=False)
            self.gmail = f"zs.test.{tag}@gmail.com"
            self.gmail_id = self._u(db, self.gmail, role="host")
            self.admin_email = f"root-{tag}@{self.domain}"
            self.admin_id = self._u(db, self.admin_email, role="super_admin")
            self.other_email = f"other-{tag}@{self.domain}"
            self.other_id = self._u(db, self.other_email, role="org_admin", org_id=other.id)
            org.owner_user_id = self.user_id
            other.owner_user_id = self.other_id
            db.commit()
        finally:
            db.close()
        self.client = TestClient(m.app)
        self.exchange_calls = []
        self.id_token = None          # what the stubbed token endpoint returns
        self.exchange_response = None  # override: a _TokenResponse

    def _u(self, db, email, *, role="host", is_active=True, email_verified=True, org_id=None):
        user = User(org_id=org_id or self.org_id, full_name="Google Person", role=role,
                    is_active=is_active, email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=email_verified,
                    email_verified_at=_now() if email_verified else None)
        db.add(user)
        db.flush()
        return user.id

    def user_ids(self):
        return [self.user_id, self.host_id, self.disabled_id, self.unverified_id, self.gmail_id,
                self.admin_id, self.other_id]

    # ── links, written directly (the link FLOW has its own tests below) ────────────────────
    def link(self, user_id, sub, email):
        db = SessionLocal()
        try:
            db.add(UserIdentity(provider="google", subject=sub, user_id=user_id, email_at_link_time=email))
            db.commit()
        finally:
            db.close()

    def links_of(self, user_id):
        db = SessionLocal()
        try:
            return db.query(UserIdentity).filter(UserIdentity.user_id == user_id).all()
        finally:
            db.close()

    # ── tokens ─────────────────────────────────────────────────────────────────────────────
    def claims(self, nonce, **over):
        now = int(time.time())
        c = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": "google-sub-admin",
            "email": self.email,
            "email_verified": True,
            "hd": self.domain,
            "name": "Google Person",
            "nonce": nonce,
            "iat": now,
            "exp": now + 600,
        }
        c.update(over)
        return {k: v for k, v in c.items() if v is not None}

    # ── sign-in flow ───────────────────────────────────────────────────────────────────────
    def start(self):
        ratelimit._HITS.clear()
        r = self.client.post("/api/auth/google/start")
        assert r.status_code == 200, r.text
        body = r.json()
        query = parse_qs(urlparse(body["authorization_url"]).query)
        return body, {k: v[0] for k, v in query.items()}

    def callback(self, *, state, code_verifier, code="auth-code-xyz", remember=False):
        ratelimit._HITS.clear()
        return self.client.post(
            "/api/auth/google/callback",
            json={"code": code, "state": state, "code_verifier": code_verifier, "remember": remember},
            headers={"User-Agent": UA},
        )

    def sign_in_with(self, **claim_overrides):
        """Full sign-in plumbing; `claim_overrides` reshapes the ID token Google returns."""
        body, query = self.start()
        self.id_token = _sign(self.claims(query["nonce"], **claim_overrides))
        return self.callback(state=body["state"], code_verifier=body["code_verifier"])

    # ── signed-in helpers for the link flow ────────────────────────────────────────────────
    def password_login(self, email):
        ratelimit._HITS.clear()
        r = self.client.post("/api/auth/login", json={"identifier": email, "password": PASSWORD},
                             headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    def step_up(self, token, purpose="google_link"):
        ratelimit._HITS.clear()
        r = self.client.post("/api/auth/step-up", json={"password": PASSWORD, "purpose": purpose},
                             headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        return r.json()["reference"]

    def link_start(self, token, reference=None):
        ratelimit._HITS.clear()
        headers = {"Authorization": f"Bearer {token}"}
        if reference:
            headers["X-Step-Up"] = reference
        return self.client.post("/api/auth/google/link/start", headers=headers)

    def link_complete(self, token, body, **claim_overrides):
        """Finish a link whose /link/start answered `body`; the token Google returns carries
        that attempt's nonce plus `claim_overrides`."""
        nonce = parse_qs(urlparse(body["authorization_url"]).query)["nonce"][0]
        self.id_token = _sign(self.claims(nonce, **claim_overrides))
        ratelimit._HITS.clear()
        return self.client.post(
            "/api/auth/google/link/complete",
            json={"code": "link-code", "state": body["state"], "code_verifier": body["code_verifier"]},
            headers={"Authorization": f"Bearer {token}"},
        )

    def fake_token_endpoint(self, url, data=None, headers=None, timeout=None, **kw):
        self.exchange_calls.append({"url": url, "data": dict(data or {})})
        if self.exchange_response is not None:
            return self.exchange_response
        return _TokenResponse(200, {"id_token": self.id_token, "token_type": "Bearer"})

    def cleanup(self):
        db = SessionLocal()
        try:
            ids = self.user_ids()
            for org_id in (self.org_id, self.other_org_id):
                org = db.get(Organization, org_id)
                if org is not None:
                    org.owner_user_id = None
            db.commit()
            db.query(AuditLog).filter(AuditLog.target_id.in_([str(i) for i in ids])).delete(synchronize_session=False)
            db.query(StepUpGrant).filter(StepUpGrant.user_id.in_(ids)).delete(synchronize_session=False)
            db.query(SignInEvent).filter(SignInEvent.user_id.in_(ids)).delete(synchronize_session=False)
            db.query(AuthSession).filter(AuthSession.user_id.in_(ids)).delete(synchronize_session=False)
            db.query(UserIdentity).filter(UserIdentity.user_id.in_(ids)).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
            db.commit()
            for org_id in (self.org_id, self.other_org_id):
                org = db.get(Organization, org_id)
                if org is not None:
                    db.delete(org)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


@pytest.fixture
def configured(monkeypatch, w):
    """Credentials present, Google's keys = our test key, Google's token endpoint = stub."""
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setattr(settings, "GOOGLE_REDIRECT_URI", REDIRECT_URI)
    monkeypatch.setattr(google_oauth, "_fetch_certs", lambda: {KID: GOOGLE_PUBLIC})
    monkeypatch.setattr(google_oauth, "_post", w.fake_token_endpoint)
    return w


@pytest.fixture
def linked(configured):
    """The org admin's Google account (sub google-sub-admin) is linked."""
    configured.link(configured.user_id, "google-sub-admin", configured.email)
    return configured


@pytest.fixture
def security_mail(monkeypatch):
    """IDN-006 (security setting changed) and IDN-003 (new sign-in) sends, captured."""
    sent = {"idn006": [], "idn003": []}
    monkeypatch.setattr(email_mod, "send_security_setting_changed_email",
                        lambda to, **kw: sent["idn006"].append({"to": to, **kw}))
    monkeypatch.setattr(email_mod, "send_new_sign_in_email",
                        lambda to, **kw: sent["idn003"].append({"to": to, **kw}))
    return sent


def _detail_code(r):
    d = r.json().get("detail")
    return d.get("code") if isinstance(d, dict) else None


def _events(user_id, method=AUTH_METHOD_GOOGLE):
    db = SessionLocal()
    try:
        return db.query(SignInEvent).filter(
            SignInEvent.user_id == user_id, SignInEvent.authentication_method == method
        ).all()
    finally:
        db.close()


def _sessions(user_id):
    db = SessionLocal()
    try:
        return db.query(AuthSession).filter(AuthSession.user_id == user_id).count()
    finally:
        db.close()


def _audit(user_id, action):
    db = SessionLocal()
    try:
        return db.query(AuditLog).filter(AuditLog.target_id == str(user_id), AuditLog.action == action).all()
    finally:
        db.close()


# ══ configuration: fail closed ═══════════════════════════════════════════════════════════

@pytest.mark.parametrize("present", [(), ("GOOGLE_CLIENT_ID",), ("GOOGLE_CLIENT_ID", "GOOGLE_REDIRECT_URI")])
def test_unconfigured_start_and_callback_answer_503(monkeypatch, w, present):
    values = {"GOOGLE_CLIENT_ID": CLIENT_ID, "GOOGLE_CLIENT_SECRET": CLIENT_SECRET, "GOOGLE_REDIRECT_URI": REDIRECT_URI}
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value if name in present else "")
    contacted = []
    monkeypatch.setattr(google_oauth, "_post", lambda *a, **k: contacted.append(a))
    ratelimit._HITS.clear()

    r = w.client.post("/api/auth/google/start")
    assert r.status_code == 503 and _detail_code(r) == google_oauth.NOT_CONFIGURED

    r = w.callback(state="x", code_verifier="v" * 43)
    assert r.status_code == 503 and _detail_code(r) == google_oauth.NOT_CONFIGURED
    assert contacted == []
    assert CLIENT_SECRET not in r.text


def test_start_returns_a_pkce_consent_url_and_never_exposes_the_secret(configured):
    body, q = configured.start()
    assert body["authorization_url"].startswith(google_oauth.AUTHORIZATION_ENDPOINT + "?")
    assert q["client_id"] == CLIENT_ID and q["redirect_uri"] == REDIRECT_URI
    assert q["response_type"] == "code" and set(q["scope"].split()) == {"openid", "email", "profile"}
    assert q["state"] == body["state"] and q["nonce"]
    assert q["code_challenge_method"] == "S256"
    expected = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == expected
    assert body["code_verifier"] not in body["authorization_url"]
    assert CLIENT_SECRET not in str(body)


# ══ domain authority (D1) — pure policy ══════════════════════════════════════════════════

@pytest.mark.parametrize("email,hd,expected", [
    ("someone@gmail.com", None, True),                      # Gmail: Google is authoritative
    ("Someone@GMAIL.com", None, True),
    ("alex@acme.example", "acme.example", True),            # Workspace, hd matches
    ("alex@acme.example", "ACME.example ", True),
    ("alex@acme.example", None, False),                     # non-Gmail without hd
    ("alex@acme.example", "other.example", False),          # hd mismatch (alias/secondary domain)
    ("alex@outlook.com", None, False),                      # domain Google does not host
    ("alex@gmail.com.evil.example", None, False),           # suffix tricks are not Gmail
    ("alex@evilgmail.com", None, False),
    ("alex@googlemail.com", None, False),                   # not asserted authoritative: fail closed
    ("alex@gmail.com", "evil.example", False),              # an hd that is not the email's domain
    ("alex@acme.example", "", False),
    ("alex@acme.example", ["acme.example"], False),         # malformed hd
    ("not-an-email", None, False),
    ("@gmail.com", None, False),
])
def test_google_is_authoritative_only_for_gmail_and_matching_workspace(email, hd, expected):
    assert google_oauth.google_is_authoritative(email, hd) is expected


# ══ sign-in: identity through `sub` only (D2) ════════════════════════════════════════════

def test_linked_workspace_account_gets_a_normal_session(linked, security_mail):
    w = linked
    r = w.sign_in_with()
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["token_type"] == "bearer" and data["access_token"]
    assert data["user"]["email"] == w.email
    assert data["user"]["role"] == "org_admin"                       # role unchanged
    assert str(data["user"]["org_id"]) == str(w.org_id)              # tenant unchanged

    me = w.client.get("/api/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == 200 and me.json()["id"] == str(w.user_id)

    (call,) = w.exchange_calls
    assert call["url"] == google_oauth.TOKEN_ENDPOINT
    assert call["data"]["client_secret"] == CLIENT_SECRET
    assert call["data"]["redirect_uri"] == REDIRECT_URI
    assert call["data"]["grant_type"] == "authorization_code"
    assert len(call["data"]["code_verifier"]) >= 43

    assert any(e.outcome == "success" for e in _events(w.user_id))   # stored as "google"
    (link,) = w.links_of(w.user_id)
    assert link.last_used_at is not None

    # IDN-003 shows a readable method; the stored event keeps the machine value.
    (mail,) = security_mail["idn003"]
    assert mail["to"] == w.email and mail["authentication_method"] == "Google sign-in"


def test_linked_gmail_account_signs_in(configured):
    w = configured
    w.link(w.gmail_id, "google-sub-gmail", w.gmail)
    r = w.sign_in_with(sub="google-sub-gmail", email=w.gmail.upper(), hd=None)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == w.gmail


def test_matching_email_without_a_link_is_refused(configured):
    """The old behaviour — an email match signs you in — is gone."""
    w = configured
    r = w.sign_in_with()                                           # w.email exists, no link
    assert r.status_code == 403 and _detail_code(r) == "GOOGLE_NOT_LINKED"
    assert "access_token" not in r.text
    assert _sessions(w.user_id) == 0
    assert w.links_of(w.user_id) == []                             # and no link was created


def test_not_linked_answer_is_identical_for_known_and_unknown_email(configured):
    w = configured
    known = w.sign_in_with(sub="sub-a")
    unknown = w.sign_in_with(sub="sub-b", email=f"nobody-{uuid.uuid4().hex[:6]}@{w.domain}")
    assert known.status_code == unknown.status_code == 403
    assert known.json() == unknown.json()
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.email.like(f"nobody-%@{w.domain}")).count() == 0
    finally:
        db.close()


def test_a_link_follows_the_sub_even_when_the_google_email_changes(linked):
    w = linked
    r = w.sign_in_with(email=f"renamed-{uuid.uuid4().hex[:6]}@{w.domain}")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["id"] == str(w.user_id)


def test_a_reassigned_address_with_a_new_sub_cannot_take_over_the_account(linked):
    """Workspace admin gave alex@ to someone else: same email, different Google account."""
    r = linked.sign_in_with(sub="google-sub-new-holder")
    assert r.status_code == 403 and _detail_code(r) == "GOOGLE_NOT_LINKED"
    assert _sessions(linked.user_id) == 0


def test_tenant_isolation_the_link_decides_the_account_never_the_email(linked):
    """A token naming org B's admin by email but carrying org A's linked `sub` signs in org
    A's admin — the email cannot redirect a sign-in into another tenant."""
    w = linked
    r = w.sign_in_with(email=w.other_email)
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["id"] == str(w.user_id) and str(user["org_id"]) == str(w.org_id)
    assert _sessions(w.other_id) == 0


# ══ domain authority at sign-in ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("overrides", [
    {"hd": None},                                     # Workspace address, hd missing
    {"hd": "someone-else.example"},                  # hd does not match
    {"email": "linked@outlook.com", "hd": None},     # domain Google does not host
])
def test_non_authoritative_domains_are_refused_even_when_linked(linked, overrides):
    r = linked.sign_in_with(**overrides)
    assert r.status_code == 403 and _detail_code(r) == google_oauth.DOMAIN_UNVERIFIED
    assert "access_token" not in r.text
    assert _sessions(linked.user_id) == 0


# ══ state / CSRF ═════════════════════════════════════════════════════════════════════════

def test_tampered_state_is_refused_before_google_is_contacted(configured):
    w = configured
    body, _ = w.start()
    forged = jose_jwt.encode({"purpose": "google_oauth_state", "nonce": "n", "cvh": "x",
                              "exp": int(time.time()) + 600}, "not-our-secret", algorithm="HS256")
    for state in (forged, body["state"][:-4] + "abcd", "not-a-token"):
        r = w.callback(state=state, code_verifier=body["code_verifier"])
        assert r.status_code == 400 and _detail_code(r) == google_oauth.INVALID_STATE
    assert w.exchange_calls == []


def test_expired_state_is_refused(configured):
    w = configured
    old = google_oauth.build_authorization_request(now=_now() - timedelta(minutes=11))
    r = w.callback(state=old.state, code_verifier=old.code_verifier)
    assert r.status_code == 400 and _detail_code(r) == google_oauth.INVALID_STATE
    assert w.exchange_calls == []


def test_state_from_another_attempt_or_wrong_verifier_is_refused(configured):
    w = configured
    a, _ = w.start()
    b, _ = w.start()
    r = w.callback(state=a["state"], code_verifier=b["code_verifier"])
    assert r.status_code == 400 and _detail_code(r) == google_oauth.INVALID_STATE
    assert w.exchange_calls == []


# ══ Google's answers ═════════════════════════════════════════════════════════════════════

def test_google_refusing_the_code_issues_no_session(linked):
    w = linked
    w.exchange_response = _TokenResponse(400, {"error": "invalid_grant", "error_description": "Bad Request"})
    body, _ = w.start()
    r = w.callback(state=body["state"], code_verifier=body["code_verifier"])
    assert r.status_code == 401 and _detail_code(r) == google_oauth.EXCHANGE_FAILED
    assert "access_token" not in r.text and "invalid_grant" not in r.text


@pytest.mark.parametrize("case", ["forged_signature", "unknown_kid", "expired", "wrong_audience",
                                  "wrong_issuer", "wrong_nonce", "no_sub", "no_nonce",
                                  "audience_list", "alg_none"])
def test_invalid_id_tokens_are_refused(linked, case):
    w = linked
    body, q = w.start()
    claims = w.claims(q["nonce"])
    key, kid = GOOGLE_PRIVATE, KID
    if case == "forged_signature":
        key = ATTACKER_PRIVATE
    elif case == "unknown_kid":
        kid = "some-other-kid"
    elif case == "expired":
        claims.update(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)
    elif case == "wrong_audience":
        claims["aud"] = "someone-else.apps.googleusercontent.com"
    elif case == "audience_list":
        claims["aud"] = ["someone-else", CLIENT_ID]
    elif case == "wrong_issuer":
        claims["iss"] = "https://evil.example.com"
    elif case == "wrong_nonce":
        claims["nonce"] = "replayed-from-another-attempt"
    elif case == "no_nonce":
        claims.pop("nonce")
    elif case == "no_sub":
        claims.pop("sub")
    if case == "alg_none":
        enc = lambda d: base64.urlsafe_b64encode(__import__("json").dumps(d).encode()).rstrip(b"=").decode()
        w.id_token = enc({"alg": "none", "kid": KID}) + "." + enc(claims) + "."
    else:
        w.id_token = _sign(claims, private_pem=key, kid=kid)

    r = w.callback(state=body["state"], code_verifier=body["code_verifier"])
    assert r.status_code == 401, (case, r.text)
    assert _detail_code(r) == google_oauth.INVALID_TOKEN
    assert "access_token" not in r.text
    assert _sessions(w.user_id) == 0


@pytest.mark.parametrize("verified", [False, "false", None])
def test_unverified_google_email_is_refused(linked, verified):
    r = linked.sign_in_with(email_verified=verified)
    assert r.status_code == 403 and _detail_code(r) == google_oauth.EMAIL_UNVERIFIED
    assert "access_token" not in r.text


def test_string_true_email_verified_is_accepted(linked):
    assert linked.sign_in_with(email_verified="true").status_code == 200


# ══ account gates — the same as password sign-in ═════════════════════════════════════════

def test_disabled_linked_account_is_refused(configured):
    w = configured
    w.link(w.disabled_id, "sub-disabled", w.disabled_email)
    r = w.sign_in_with(sub="sub-disabled", email=w.disabled_email)
    assert r.status_code == 403 and r.json()["detail"] == "Account is disabled"
    assert _sessions(w.disabled_id) == 0


def test_unverified_zoikostream_account_still_needs_its_own_verification(configured):
    w = configured
    w.link(w.unverified_id, "sub-unverified", w.unverified_email)
    r = w.sign_in_with(sub="sub-unverified", email=w.unverified_email)
    assert r.status_code == 403 and _detail_code(r) == "EMAIL_VERIFICATION_REQUIRED"
    assert "access_token" not in r.text


def test_risk_block_answers_exactly_like_a_blocked_password_sign_in(linked, monkeypatch):
    from app.models import BLOCK_SUSPICIOUS
    monkeypatch.setattr(idsec, "evaluate_sign_in", lambda db, user: BLOCK_SUSPICIOUS)
    r = linked.sign_in_with()
    assert r.status_code == 401 and r.json() == {"detail": "Invalid credentials"}
    assert _sessions(linked.user_id) == 0


# ══ super_admin (D3) — refused server-side on every Google entry point ═══════════════════

def test_super_admin_cannot_sign_in_with_google_even_with_a_link(configured):
    """A link for a super_admin cannot be made through the API; one written directly (a
    pre-existing or tampered row) still yields no session."""
    w = configured
    w.link(w.admin_id, "sub-root", w.admin_email)
    r = w.sign_in_with(sub="sub-root", email=w.admin_email)
    assert r.status_code == 403 and _detail_code(r) == "GOOGLE_ROLE_NOT_PERMITTED"
    assert "access_token" not in r.text
    assert _sessions(w.admin_id) == 0
    assert all(e.outcome != "success" for e in _events(w.admin_id))


def test_super_admin_cannot_link_google(configured):
    w = configured
    token = w.password_login(w.admin_email)                       # password still works
    r = w.link_start(token)
    assert r.status_code == 403 and _detail_code(r) == "GOOGLE_ROLE_NOT_PERMITTED"
    ratelimit._HITS.clear()
    r = w.client.post("/api/auth/google/link/complete",
                      json={"code": "c", "state": "s", "code_verifier": "v" * 43},
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403 and _detail_code(r) == "GOOGLE_ROLE_NOT_PERMITTED"
    me = w.client.get("/api/auth/google/identity", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["available"] is False
    assert w.links_of(w.admin_id) == []


def test_password_sign_in_for_super_admin_is_unchanged(configured):
    token = configured.password_login(configured.admin_email)
    me = configured.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["role"] == "super_admin"


# ══ linking (D2) ═════════════════════════════════════════════════════════════════════════

def test_link_start_requires_a_google_link_step_up(configured):
    w = configured
    token = w.password_login(w.email)
    r = w.link_start(token)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "STEP_UP_REQUIRED"
    assert r.json()["detail"]["purpose"] == "google_link"
    wrong = w.step_up(token, purpose="high_risk_role_grant")       # a grant for something else
    r = w.link_start(token, wrong)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "STEP_UP_REQUIRED"
    assert w.exchange_calls == []


def test_full_link_flow_then_sign_in(configured, security_mail):
    w = configured
    token = w.password_login(w.email)
    start = w.link_start(token, w.step_up(token))
    assert start.status_code == 200, start.text
    body = start.json()

    r = w.link_complete(token, body, sub="sub-linked-via-flow")
    assert r.status_code == 200, r.text
    assert r.json()["linked"] is True and r.json()["email"] == w.email
    assert "sub-linked-via-flow" not in r.text                    # `sub` is never returned

    (link,) = w.links_of(w.user_id)
    assert link.subject == "sub-linked-via-flow" and link.email_at_link_time == w.email

    (audit,) = _audit(w.user_id, "user.google_identity.linked")
    assert audit.outcome == "applied" and audit.org_id == w.org_id
    assert "sub-linked-via-flow" not in str(audit.meta)            # fingerprint only
    assert audit.meta["subject_fingerprint"] == hashlib.sha256(b"sub-linked-via-flow").hexdigest()[:12]
    (mail,) = security_mail["idn006"]
    assert mail["to"] == w.email and mail["change_description"] == email_mod.IDN_006_GOOGLE_LINKED

    r = w.sign_in_with(sub="sub-linked-via-flow")
    assert r.status_code == 200 and r.json()["user"]["id"] == str(w.user_id)


def test_step_up_grant_is_single_use(configured):
    w = configured
    token = w.password_login(w.email)
    reference = w.step_up(token)
    assert w.link_start(token, reference).status_code == 200
    r = w.link_start(token, reference)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "STEP_UP_REQUIRED"


def test_link_requires_the_accounts_own_email(configured):
    w = configured
    token = w.password_login(w.email)
    body = w.link_start(token, w.step_up(token)).json()
    r = w.link_complete(token, body, sub="sub-other-person", email=f"someone-else@{w.domain}")
    assert r.status_code == 409 and _detail_code(r) == "GOOGLE_EMAIL_MISMATCH"
    assert w.links_of(w.user_id) == []


def test_link_refuses_a_non_authoritative_domain(configured):
    w = configured
    token = w.password_login(w.email)
    body = w.link_start(token, w.step_up(token)).json()
    r = w.link_complete(token, body, hd=None)
    assert r.status_code == 403 and _detail_code(r) == google_oauth.DOMAIN_UNVERIFIED
    assert w.links_of(w.user_id) == []


def test_a_google_account_linked_elsewhere_cannot_be_linked_again(configured):
    w = configured
    w.link(w.host_id, "sub-taken", w.host_email)
    token = w.password_login(w.email)
    body = w.link_start(token, w.step_up(token)).json()
    r = w.link_complete(token, body, sub="sub-taken")
    assert r.status_code == 409 and _detail_code(r) == "GOOGLE_IDENTITY_IN_USE"
    assert w.links_of(w.user_id) == []
    assert [l.user_id for l in w.links_of(w.host_id)] == [w.host_id]


def test_an_existing_link_is_never_replaced_automatically(linked):
    w = linked
    token = w.password_login(w.email)
    # Refused at start, before the step-up grant is spent.
    reference = w.step_up(token)
    r = w.link_start(token, reference)
    assert r.status_code == 409 and _detail_code(r) == "GOOGLE_ALREADY_LINKED"
    db = SessionLocal()
    try:
        grant = db.query(StepUpGrant).filter(StepUpGrant.user_id == w.user_id).one()
        assert grant.consumed_at is None
    finally:
        db.close()
    (link,) = w.links_of(w.user_id)
    assert link.subject == "google-sub-admin"


def test_a_race_to_a_second_google_account_is_refused_at_completion(configured):
    """Started while unlinked; a link appears before completion (another tab). The second
    Google account is refused and the first link stands."""
    w = configured
    token = w.password_login(w.email)
    body = w.link_start(token, w.step_up(token)).json()
    w.link(w.user_id, "sub-first", w.email)
    r = w.link_complete(token, body, sub="sub-second")
    assert r.status_code == 409 and _detail_code(r) == "GOOGLE_ALREADY_LINKED"
    assert [l.subject for l in w.links_of(w.user_id)] == ["sub-first"]


def test_relinking_the_same_google_account_is_idempotent(configured):
    w = configured
    token = w.password_login(w.email)
    body = w.link_start(token, w.step_up(token)).json()
    w.link(w.user_id, "sub-same", w.email)
    r = w.link_complete(token, body, sub="sub-same")
    assert r.status_code == 200 and r.json()["linked"] is True
    assert len(w.links_of(w.user_id)) == 1


def test_link_and_sign_in_states_are_not_interchangeable(configured):
    w = configured
    token = w.password_login(w.email)
    link_body = w.link_start(token, w.step_up(token)).json()
    # A link state cannot complete a sign-in…
    r = w.callback(state=link_body["state"], code_verifier=link_body["code_verifier"])
    assert r.status_code == 400 and _detail_code(r) == google_oauth.INVALID_STATE
    # …and a sign-in state cannot create a link.
    sign_in_body, _ = w.start()
    r = w.link_complete(token, sign_in_body, sub="sub-x")
    assert r.status_code == 400 and _detail_code(r) == google_oauth.INVALID_STATE
    assert w.exchange_calls == [] and w.links_of(w.user_id) == []


def test_a_link_state_only_completes_in_the_session_that_started_it(configured):
    w = configured
    token = w.password_login(w.email)
    body = w.link_start(token, w.step_up(token)).json()
    other_session = w.password_login(w.email)                     # same user, another device
    r = w.link_complete(other_session, body, sub="sub-y")
    assert r.status_code == 400 and _detail_code(r) == google_oauth.INVALID_STATE
    other_user = w.password_login(w.host_email)                   # a different account
    r = w.link_complete(other_user, body, sub="sub-y", email=w.host_email)
    assert r.status_code == 400 and _detail_code(r) == google_oauth.INVALID_STATE
    assert w.exchange_calls == []
    assert w.links_of(w.user_id) == [] and w.links_of(w.host_id) == []


def test_link_endpoints_require_authentication(configured):
    ratelimit._HITS.clear()
    assert configured.client.post("/api/auth/google/link/start").status_code in (401, 403)
    assert configured.client.get("/api/auth/google/identity").status_code in (401, 403)
    assert configured.client.delete("/api/auth/google/identity").status_code in (401, 403)


def test_identity_status_never_exposes_the_sub(linked):
    w = linked
    token = w.password_login(w.email)
    r = w.client.get("/api/auth/google/identity", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True and data["linked"] is True and data["email"] == w.email
    assert "google-sub-admin" not in r.text


# ══ unlinking ════════════════════════════════════════════════════════════════════════════

def test_unlink_removes_the_way_in_and_is_recorded(linked, security_mail):
    w = linked
    token = w.password_login(w.email)
    r = w.client.delete("/api/auth/google/identity", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["linked"] is False
    assert w.links_of(w.user_id) == []
    (audit,) = _audit(w.user_id, "user.google_identity.unlinked")
    assert "google-sub-admin" not in str(audit.meta)
    (mail,) = security_mail["idn006"]
    assert mail["change_description"] == email_mod.IDN_006_GOOGLE_UNLINKED

    r = w.sign_in_with()
    assert r.status_code == 403 and _detail_code(r) == "GOOGLE_NOT_LINKED"

    r = w.client.delete("/api/auth/google/identity", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


# ══ nothing else changed ═════════════════════════════════════════════════════════════════

def test_password_sign_in_is_unchanged(linked):
    ratelimit._HITS.clear()
    r = linked.client.post("/api/auth/login", json={"identifier": linked.email, "password": PASSWORD},
                           headers={"User-Agent": UA})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == linked.email
    assert linked.exchange_calls == []
    assert any(e.outcome == "success" for e in _events(linked.user_id, method="password"))
