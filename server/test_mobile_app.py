"""The server-side contract the Android app depends on.

Three things had to change on this side for the app to work at all, and each one fails
SILENTLY when it is wrong — which is the only reason this file exists. None of the three
produces an error, a log line, or a support ticket. They produce an app that installs, opens,
and then quietly does not work:

  * /.well-known/assetlinks.json  wrong, and every emailed link opens Chrome instead of the
                                  app, exactly as it did before the app existed.
  * MOBILE_APP_ORIGINS            missing, and every request the app makes is CORS-blocked, so
                                  nobody can sign in.
  * the claim cookie's SameSite   left at Lax, and the one-device claim on a private invite
                                  never engages cross-site — so the SECOND visit by the
                                  genuine invitee is refused as a different device.

The fourth concern here is the opposite one: MOBILE_APP_ORIGINS exists to bypass a production
guard, and a bypass nobody tests is a hole. `test_production_rejects_an_unrecognised_mobile_origin`
is the test that keeps it a narrow exemption rather than a way around the check.
"""


import pytest
from fastapi import Request

from app import config
from app.config import InsecureProductionConfig
from app.routers.events import claim_cookie_policy
from app.routers.wellknown import assetlinks


def _request(headers: dict[str, str] | None = None, scheme: str = "http") -> Request:
    """A bare ASGI request. Enough for the header/scheme reads under test and nothing else."""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": scheme,
        "path": "/",
        "headers": raw,
        "query_string": b"",
    })


def _body(response) -> list:
    import json
    return json.loads(response.body)


# ── /.well-known/assetlinks.json ────────────────────────────────────────────────────────────

def test_assetlinks_is_an_empty_list_when_no_app_is_configured(monkeypatch):
    """Empty list, not a 404.

    A 404 here reaches the SPA catch-all in main.py and comes back as index.html with a 200.
    Android reads that as a MALFORMED statement rather than an absent one, which is a
    different and more confusing failure than "this domain has no app".
    """
    monkeypatch.setattr(config.settings, "ANDROID_PACKAGE_NAME", "")
    monkeypatch.setattr(config.settings, "ANDROID_CERT_FINGERPRINTS", "")

    response = assetlinks()

    assert _body(response) == []
    assert response.media_type == "application/json"


def test_assetlinks_publishes_the_package_and_fingerprint_when_configured(monkeypatch):
    monkeypatch.setattr(config.settings, "ANDROID_PACKAGE_NAME", "com.zoikostream.app")
    monkeypatch.setattr(
        config.settings, "ANDROID_CERT_FINGERPRINTS",
        "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:"
        "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99")

    statements = _body(assetlinks())

    assert len(statements) == 1
    statement = statements[0]
    assert statement["target"]["namespace"] == "android_app"
    assert statement["target"]["package_name"] == "com.zoikostream.app"
    assert len(statement["target"]["sha256_cert_fingerprints"]) == 1

    # handle_all_urls is what grants the app its links. get_login_creds shares the domain's
    # SAVED PASSWORDS with the app, and this app has no autofill integration that would
    # justify it — so its absence is an assertion, not an omission.
    assert statement["relation"] == ["delegate_permission/common.handle_all_urls"]


def test_assetlinks_accepts_several_fingerprints(monkeypatch):
    """Two keys have to coexist during a signing-key rotation, and while a locally-signed
    build and a Play-signed build are both in circulation — which is every beta."""
    monkeypatch.setattr(config.settings, "ANDROID_PACKAGE_NAME", "com.zoikostream.app")
    monkeypatch.setattr(config.settings, "ANDROID_CERT_FINGERPRINTS", "AA:BB, CC:DD")

    fingerprints = _body(assetlinks())[0]["target"]["sha256_cert_fingerprints"]

    assert fingerprints == ["AA:BB", "CC:DD"]


def test_assetlinks_uppercases_fingerprints(monkeypatch):
    """Android compares these as uppercase hex, and every tool that PRINTS one — keytool,
    gradle signingReport, the Play Console — prints lowercase. An operator pasting the value
    from where they read it is the expected case, not a mistake, and normalising it here is
    cheaper than a support thread about links opening in the wrong place."""
    monkeypatch.setattr(config.settings, "ANDROID_PACKAGE_NAME", "com.zoikostream.app")
    monkeypatch.setattr(config.settings, "ANDROID_CERT_FINGERPRINTS", "aa:bb:cc")

    assert _body(assetlinks())[0]["target"]["sha256_cert_fingerprints"] == ["AA:BB:CC"]


def test_assetlinks_is_empty_when_only_half_configured(monkeypatch):
    """A package with no fingerprint is not a weaker statement, it is an invalid one. Publish
    nothing rather than something Android will reject."""
    monkeypatch.setattr(config.settings, "ANDROID_PACKAGE_NAME", "com.zoikostream.app")
    monkeypatch.setattr(config.settings, "ANDROID_CERT_FINGERPRINTS", "")

    assert _body(assetlinks()) == []


# ── the claim cookie ────────────────────────────────────────────────────────────────────────

def test_claim_cookie_is_cross_site_capable_over_https():
    """SameSite=None + Secure.

    The app's WebView origin is https://localhost and the API is the public host, so every
    request from it is cross-site. A Lax cookie would be accepted and then never sent back —
    and because the claim is only CHECKED on the second visit, the damage shows up as the
    genuine invitee being refused their own event as though they were a second device.
    """
    secure, samesite = claim_cookie_policy(_request({"x-forwarded-proto": "https"}))

    assert secure is True
    assert samesite == "none"


def test_claim_cookie_stays_lax_over_plain_http():
    """Browsers reject SameSite=None without Secure outright, which would DROP the cookie
    rather than downgrade it — so http development has to keep Lax or the claim never gets
    stored at all."""
    secure, samesite = claim_cookie_policy(_request())

    assert secure is False
    assert samesite == "lax"


def test_claim_cookie_trusts_the_forwarded_proto_over_the_socket_scheme():
    """Cloud Run terminates TLS and forwards over plain HTTP, so request.url.scheme reads
    "http" on every production request. Reading the socket scheme alone would mark the cookie
    insecure and Lax in production — which is the whole failure this header exists to
    prevent."""
    secure, samesite = claim_cookie_policy(
        _request({"x-forwarded-proto": "https"}, scheme="http"))

    assert (secure, samesite) == (True, "none")


# ── MOBILE_APP_ORIGINS, and the guard it is allowed to bypass ───────────────────────────────

def _production_env(monkeypatch, **overrides):
    """A minimally-valid production configuration, plus whatever the test is varying."""
    values = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "a-real-looking-secret-value-for-tests-only",
        "APP_URL": "https://get.zoikostream.com",
        "CORS_ORIGINS": "https://get.zoikostream.com",
        "MOBILE_APP_ORIGINS": "",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setattr(config.settings, key, value)


def test_production_accepts_the_capacitor_webview_origin(monkeypatch):
    _production_env(monkeypatch, MOBILE_APP_ORIGINS="https://localhost")

    config._validate_production_config()  # must not raise


def test_production_rejects_an_unrecognised_mobile_origin(monkeypatch):
    """THE test that keeps this setting an exemption rather than a hole.

    MOBILE_APP_ORIGINS exists to put `https://localhost` on a credentialed CORS allowlist in
    production — something CORS_ORIGINS is (correctly) forbidden to do. Without this check it
    would be a general-purpose way to put ANY origin there while bypassing the guard written
    to stop exactly that.
    """
    _production_env(monkeypatch, MOBILE_APP_ORIGINS="https://attacker.example")

    with pytest.raises(InsecureProductionConfig) as raised:
        config._validate_production_config()

    assert "MOBILE_APP_ORIGINS" in str(raised.value)


def test_production_still_rejects_a_localhost_cors_origin(monkeypatch):
    """The original guard, unweakened. The mobile exemption is a SEPARATE setting precisely so
    that adding it could not loosen this one."""
    _production_env(
        monkeypatch,
        CORS_ORIGINS="https://get.zoikostream.com,http://localhost:5173",
        MOBILE_APP_ORIGINS="https://localhost",
    )

    with pytest.raises(InsecureProductionConfig) as raised:
        config._validate_production_config()

    assert "CORS_ORIGINS" in str(raised.value)


def test_known_mobile_origins_are_the_two_capacitor_schemes():
    """A closed set, asserted so that widening it is a deliberate edit with a test to update
    rather than something that drifts. Both are the app's own bundle inside its WebView — no
    port is bound and nothing else on the device or the network can answer on them, which is
    the entire basis for letting them past the localhost rule."""
    assert config.KNOWN_MOBILE_APP_ORIGINS == frozenset({
        "https://localhost",       # androidScheme: "https" — what this app uses
        "capacitor://localhost",   # the iOS default, so an iOS build needs no change here
    })


def test_cors_allowlist_includes_the_mobile_origins(monkeypatch):
    """main.py unions MOBILE_APP_ORIGINS into allow_origins. Asserted by rebuilding the same
    expression rather than by importing main (which starts a lifespan and 14 tickers)."""
    monkeypatch.setattr(config.settings, "CORS_ORIGINS", "https://get.zoikostream.com")
    monkeypatch.setattr(config.settings, "MOBILE_APP_ORIGINS", "https://localhost")

    origins = [
        o.strip() for o in
        f"{config.settings.CORS_ORIGINS},{config.settings.MOBILE_APP_ORIGINS}".split(",")
        if o.strip()
    ]

    assert origins == ["https://get.zoikostream.com", "https://localhost"]


def test_mobile_origins_default_to_empty():
    """A deployment with no app must not carry an extra credentialed origin it never asked
    for. Read off the field declaration rather than the live `settings` object, which the
    tests above monkeypatch and which a real deployment configures from the environment."""
    assert config.Settings.model_fields["MOBILE_APP_ORIGINS"].default == ""
