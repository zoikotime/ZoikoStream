"""PKCE code_verifier validation (RFC 7636 §4.1) for Google sign-in and linking. DB-FREE.

    code_verifier = 43*128unreserved     unreserved = ALPHA / DIGIT / "-" / "." / "_" / "~"

A malformed verifier used to pass the request schemas and then raise UnicodeEncodeError while
the service hashed it: an HTTP 500 for any unauthenticated caller. Now:

    schemas     GoogleCallbackIn and GoogleLinkCompleteIn reject it -> the normal 422
    request     POST /api/auth/google/callback answers 422 before the handler runs
    service     _decode_state refuses it (GOOGLE_STATE_INVALID) BEFORE any decoding or hashing
    unchanged   the verifiers the service mints, S256, and the full happy path still work

Nothing here opens a database connection: every request test asserts the engine handed out no
connection, and requests use a platform host so custom-domain routing never looks one up.
"""
import hashlib
import base64
import secrets
import string
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import crypt as google_crypt
from google.auth import jwt as google_jwt
from jose import jwt as jose_jwt
from pydantic import ValidationError
from sqlalchemy import event
from starlette.testclient import TestClient

import app.main as m
from app import ratelimit
from app.config import settings
from app.db import engine
from app.routers import auth as auth_router
from app.schemas import GoogleCallbackIn, GoogleLinkCompleteIn
from app.schemas.auth import PKCE_CODE_VERIFIER_PATTERN
from app.services import google_oauth

UNRESERVED = string.ascii_letters + string.digits + "-._~"

VALID = {
    "43 chars (minimum)": "a" * 43,
    "128 chars (maximum)": "Z" * 128,
    "every unreserved character": (UNRESERVED * 2)[:128],
    "what the service mints": secrets.token_urlsafe(64),
}
INVALID = {
    "empty": "",
    "42 chars": "a" * 42,
    "129 chars": "a" * 129,
    "non-ASCII letters": "é" * 43,
    "non-ASCII inside an otherwise valid value": "a" * 21 + "ü" + "a" * 21,
    "emoji": "a" * 42 + "\U0001F600",
    "space": "a" * 21 + " " + "a" * 21,
    "tab": "a" * 21 + "\t" + "a" * 21,
    "leading space": " " + "a" * 43,
    "trailing newline": "a" * 43 + "\n",
    "plus": "a" * 42 + "+",
    "slash": "a" * 42 + "/",
    "equals padding": "a" * 42 + "=",
    "exclamation": "a" * 42 + "!",
    "percent-encoding": "a" * 40 + "%41",
}
SCHEMAS = [GoogleCallbackIn, GoogleLinkCompleteIn]


def _payload(verifier):
    return {"code": "auth-code", "state": "some-state", "code_verifier": verifier}


# ── the schemas ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda s: s.__name__)
@pytest.mark.parametrize("verifier", VALID.values(), ids=VALID.keys())
def test_schemas_accept_rfc7636_verifiers(schema, verifier):
    assert schema(**_payload(verifier)).code_verifier == verifier


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda s: s.__name__)
@pytest.mark.parametrize("verifier", INVALID.values(), ids=INVALID.keys())
def test_schemas_reject_everything_else(schema, verifier):
    with pytest.raises(ValidationError) as caught:
        schema(**_payload(verifier))
    (error,) = caught.value.errors()
    assert error["loc"] == ("code_verifier",)
    assert error["type"] in {"string_pattern_mismatch", "string_too_short", "string_too_long"}


def test_the_pattern_is_rfc7636():
    assert PKCE_CODE_VERIFIER_PATTERN == r"^[A-Za-z0-9._~-]{43,128}$"


def test_both_google_endpoints_take_the_validated_schemas():
    import inspect
    assert inspect.signature(auth_router.google_callback).parameters["data"].annotation is GoogleCallbackIn
    assert inspect.signature(auth_router.google_link_complete).parameters["data"].annotation is GoogleLinkCompleteIn
    routes = {r.path: r.endpoint for r in m.app.routes if hasattr(r, "endpoint")}
    assert routes["/api/auth/google/callback"] is auth_router.google_callback
    assert routes["/api/auth/google/link/complete"] is auth_router.google_link_complete


# ── the request: an ordinary 422, before any OAuth processing, without a database ───────

@pytest.fixture
def client(monkeypatch):
    """The real app, addressed by a platform host. Records every OAuth-processing call and
    every database checkout, so a test can prove neither happened."""
    seen = {"decode_state": 0, "exchange": 0, "db_checkouts": 0}

    def count_checkout(*_):
        seen["db_checkouts"] += 1

    real_decode = google_oauth._decode_state

    def decode_spy(*a, **k):
        seen["decode_state"] += 1
        return real_decode(*a, **k)

    monkeypatch.setattr(google_oauth, "_decode_state", decode_spy)
    monkeypatch.setattr(google_oauth, "_post", lambda *a, **k: seen.__setitem__("exchange", seen["exchange"] + 1))
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-secret-not-real")
    monkeypatch.setattr(settings, "GOOGLE_REDIRECT_URI", "http://localhost:5173/auth/google/callback")
    event.listen(engine, "checkout", count_checkout)
    try:
        yield TestClient(m.app, base_url="https://app.zoikostream.com", raise_server_exceptions=False), seen
    finally:
        event.remove(engine, "checkout", count_checkout)


@pytest.mark.parametrize("verifier", INVALID.values(), ids=INVALID.keys())
def test_callback_answers_422_before_any_oauth_processing(client, verifier):
    http, seen = client
    ratelimit._HITS.clear()
    r = http.post("/api/auth/google/callback", json=_payload(verifier))
    assert r.status_code == 422, r.text
    body = r.json()
    assert isinstance(body["detail"], list)                                   # FastAPI's structured shape
    assert any(e["loc"] == ["body", "code_verifier"] for e in body["detail"])
    assert "Traceback" not in r.text and "UnicodeEncodeError" not in r.text
    assert seen == {"decode_state": 0, "exchange": 0, "db_checkouts": 0}


@pytest.mark.parametrize("verifier", VALID.values(), ids=VALID.keys())
def test_valid_verifiers_still_reach_the_state_check(client, verifier):
    """Validation passes, so the request reaches OAuth processing — where this made-up state
    is refused as usual (400), still without Google or a database."""
    http, seen = client
    ratelimit._HITS.clear()
    r = http.post("/api/auth/google/callback", json=_payload(verifier))
    assert r.status_code == 400 and r.json()["detail"]["code"] == google_oauth.INVALID_STATE
    assert seen == {"decode_state": 1, "exchange": 0, "db_checkouts": 0}


# ── the service: refused before decoding or hashing ──────────────────────────────────────

@pytest.mark.parametrize("verifier", list(INVALID.values()) + [None, 12345], ids=list(INVALID.keys()) + ["None", "int"])
def test_service_refuses_malformed_verifiers_before_any_crypto(monkeypatch, verifier):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-secret-not-real")
    monkeypatch.setattr(settings, "GOOGLE_REDIRECT_URI", "http://localhost:5173/auth/google/callback")
    state = google_oauth.build_authorization_request().state
    touched = []
    monkeypatch.setattr(google_oauth.jose_jwt, "decode", lambda *a, **k: touched.append("decode"))
    monkeypatch.setattr(google_oauth, "_verifier_hash", lambda v: touched.append("hash"))
    with pytest.raises(google_oauth.GoogleOAuthError) as caught:
        google_oauth._decode_state(state, verifier)
    assert caught.value.code == google_oauth.INVALID_STATE
    assert touched == []


# ── valid OAuth behaviour is unchanged ───────────────────────────────────────────────────

def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode(),
            key.public_key().public_bytes(serialization.Encoding.PEM,
                                          serialization.PublicFormat.SubjectPublicKeyInfo).decode())


@pytest.mark.parametrize("purpose", [google_oauth.STATE_PURPOSE, google_oauth.STATE_PURPOSE_LINK])
def test_minted_verifiers_validate_and_the_full_s256_flow_still_succeeds(monkeypatch, purpose):
    client_id = "test-client.apps.googleusercontent.com"
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", client_id)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-secret-not-real")
    monkeypatch.setattr(settings, "GOOGLE_REDIRECT_URI", "http://localhost:5173/auth/google/callback")
    private, public = _keypair()
    monkeypatch.setattr(google_oauth, "_fetch_certs", lambda: {"kid-1": public})
    bound = {"user_id": "u-1", "session_id": "s-1"} if purpose == google_oauth.STATE_PURPOSE_LINK else {}

    for _ in range(25):                                   # every minted verifier is valid input
        v = google_oauth.build_authorization_request(purpose=purpose, **bound).code_verifier
        GoogleCallbackIn(**_payload(v))
        GoogleLinkCompleteIn(**_payload(v))

    req = google_oauth.build_authorization_request(purpose=purpose, **bound)
    challenge = dict(p.split("=", 1) for p in req.authorization_url.split("?", 1)[1].split("&"))["code_challenge"]
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(req.code_verifier.encode()).digest()).rstrip(b"=").decode()

    nonce = jose_jwt.get_unverified_claims(req.state)["nonce"]
    now = int(time.time())
    id_token = google_jwt.encode(
        google_crypt.RSASigner.from_string(private, key_id="kid-1"),
        {"iss": "https://accounts.google.com", "aud": client_id, "sub": "sub-1", "email": "a@gmail.com",
         "email_verified": True, "nonce": nonce, "iat": now, "exp": now + 600},
    ).decode()
    sent = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"id_token": id_token}

    monkeypatch.setattr(google_oauth, "_post", lambda url, data=None, **k: (sent.update(data), _Resp())[1])
    identity = google_oauth.complete(code="code-1", state=req.state, code_verifier=req.code_verifier,
                                     purpose=purpose, **bound)
    assert identity.sub == "sub-1" and identity.email == "a@gmail.com"
    assert sent["code_verifier"] == req.code_verifier          # the verifier still goes to Google as-is
