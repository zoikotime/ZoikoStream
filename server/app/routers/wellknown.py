"""Platform-mandated files served from /.well-known/.

Right now that means one thing: the Digital Asset Links statement that lets the Android app
own this domain's links.

── WHAT THIS FILE DECIDES ───────────────────────────────────────────────────────────────────
Whether a ZoikoStream link that lands in somebody's email opens the app or opens Chrome.

When the app is installed, Android reads the intent filters in its manifest (which claim
https://get.zoikostream.com/events/..., /e/..., /accept-invite and the rest), then fetches
this endpoint and checks that the domain agrees: that it names this exact package and this
exact signing certificate. If it does, those links belong to the app, with no "open with"
dialog. If it does not, verification fails and every one of them goes to the browser.

The part worth internalising is that the failure is SILENT. There is no error, no log, no
prompt — links simply keep opening in Chrome, which is also what they did before the app
existed, so the symptom is indistinguishable from "the feature was never shipped". The three
ways to get there:

  * the file is absent or returns anything but 200 (including a redirect — Android does not
    follow them for this fetch),
  * it is served as text/html rather than application/json, which is what a SPA catch-all
    does to an unknown path,
  * the fingerprint does not match the certificate the INSTALLED artefact is signed with.

The third is the one that bites in production. Under Play App Signing, Google re-signs every
upload with its own key, so the certificate on a user's device is Google's, not the upload
keystore's. A deployment that publishes the upload key's fingerprint verifies perfectly
against a locally-built APK and fails for every real install. The fingerprint to publish is in
Play Console -> Setup -> App signing, under "App signing key certificate".
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..config import settings

router = APIRouter(prefix="/.well-known", tags=["well-known"])


def _fingerprints() -> list[str]:
    """The configured SHA-256 certificate fingerprints, normalised.

    Android compares these as uppercase colon-separated hex. Lowercase is the form every
    tool PRINTS them in (keytool, gradle signingReport, the Play Console), so an operator
    pasting one straight from where they read it is the expected case rather than a mistake —
    normalising is cheaper than a support thread about a link that opens the wrong app.
    """
    return [
        f.strip().upper()
        for f in settings.ANDROID_CERT_FINGERPRINTS.split(",")
        if f.strip()
    ]


@router.get("/assetlinks.json", include_in_schema=False)
def assetlinks():
    """The Digital Asset Links statement list.

    Returns an empty list — valid JSON, and a complete answer meaning "no app is associated
    with this domain" — when the deployment has no app configured. That is deliberately not a
    404: a 404 here would reach the SPA catch-all in main.py on some routing orders and come
    back as index.html with a 200, which Android reads as a malformed statement rather than as
    an absent one. An explicit empty list is unambiguous in a way an error is not.
    """
    package = settings.ANDROID_PACKAGE_NAME.strip()
    fingerprints = _fingerprints()

    statements = []
    if package and fingerprints:
        statements.append({
            # handle_all_urls is what grants the app the right to open the URLs its manifest
            # claims. delegate_permission/common.get_login_creds is deliberately NOT included:
            # that one shares the domain's saved passwords with the app, and this app has no
            # password autofill integration to justify it.
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": package,
                "sha256_cert_fingerprints": fingerprints,
            },
        })

    # Explicit media type. FastAPI would send application/json anyway, but this endpoint's
    # whole contract is "Android must see JSON here", and it is the one header whose loss is
    # invisible from the browser and fatal to verification.
    return JSONResponse(
        content=statements,
        media_type="application/json",
        # Android re-fetches this on install and on updates. A day is long enough that it is
        # not hit repeatedly, short enough that rotating a signing key or adding a second one
        # takes effect without waiting on a CDN.
        headers={"Cache-Control": "public, max-age=86400"},
    )
