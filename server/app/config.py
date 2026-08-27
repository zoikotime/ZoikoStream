import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

# Single .env at the repo root (server/app/config.py -> repo root is two levels up).
ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"

# Anyone holding this value can forge a JWT for any user id and role, so it must never
# survive into a deployed environment. Kept as a default (rather than a required field) so a
# fresh clone still boots in development — but production REFUSES to start on it, see the
# validation below.
DEV_SECRET_KEY = "dev-secret-change-me"

# ENVIRONMENT values that mean "this is a real deployment serving real users". Matched
# case-insensitively after stripping, because a stray "Production " in a deployment config
# must not silently fall through to development behaviour — that would defeat every guard
# keyed off this list.
PRODUCTION_ENVIRONMENTS = ("production", "prod")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV, extra="ignore")

    DATABASE_URL: str
    SECRET_KEY: str = DEV_SECRET_KEY
    SUPER_ADMIN_EMAIL: str = "info@zoikostream.com"  # this email registers as super_admin
    ACCESS_TOKEN_HOURS: int = 24              # default session length
    REMEMBER_TOKEN_DAYS: int = 30            # "Remember for 30 days"
    # 5173 is Vite's default; 5174 is its fallback when 5173 is taken. 4173 = vite preview.
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:5174,http://localhost:4173"
    APP_URL: str = "http://localhost:5173"
    # LiveKit — ponytail: default "" so the app still boots without them; streaming
    # (services/livekit.py, /streams) needs real values, so set these in .env before using it.
    LIVEKIT_URL: str = ""
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""

    # Redis — ponytail: blank = single-process fan-out only (fine for dev and one uvicorn
    # worker). Set it to share live-event traffic + presence across workers/hosts.
    REDIS_URL: str = ""

    # GCS — recording storage. Blank = egress has no destination, so LiveKit Cloud rejects
    # the request outright (services/livekit.py surfaces that as an "unenforced" recording
    # rather than failing the host's click). GCS_CREDENTIALS_PATH points at a service
    # account JSON key file (kept outside the repo, Storage Object Admin on the bucket) —
    # this app's own reads (signed URLs, existence checks, deletes) will fall back to
    # Application Default Credentials if it's unset, but recording uploads themselves
    # always need this: LiveKit Cloud's egress workers run outside this GCP project and
    # can't use Cloud Run's attached identity. In production, mount the key from Secret
    # Manager as a file (Cloud Run -> Edit & Deploy -> Secrets -> mount as volume) and
    # point this at the mount path — never bake it into the image or commit it.
    GCS_BUCKET: str = ""
    GCS_CREDENTIALS_PATH: str = ""

    # Payments — the provider-neutral webhook's signing secret. POST
    # /commercial/webhooks/payments refuses every call while this is blank rather than
    # accepting unsigned payloads (routers/commercial.py). Separate from STRIPE_WEBHOOK_SECRET
    # below: that one is Stripe's own signature scheme on its own endpoint.
    PAYMENTS_WEBHOOK_SECRET: str = ""

    # Stripe — blank in every environment that does not use Stripe. The application must
    # start without these (nothing at import time touches them), but ASKING for the Stripe
    # provider without STRIPE_SECRET_KEY is a hard configuration error, never a silent
    # fallback to the mock provider (services/payments.get_provider).
    #
    # Use TEST-mode credentials only (sk_test_...). Never commit a real value: these are read
    # from .env, which is gitignored — see .env.example for the placeholders.
    STRIPE_SECRET_KEY: str = ""
    # Verifies the Stripe signature header on Stripe's own webhook endpoint (Phase 4B).
    STRIPE_WEBHOOK_SECRET: str = ""
    # NOT a secret — Stripe publishable keys are designed to be public and can only create
    # payment attempts, never read or move money. Held here (rather than as a VITE_ build-time
    # var) so the API can serve it to the browser at runtime and a key rotation needs no
    # frontend rebuild. Nothing on the server reads it; it exists for the payment UI.
    STRIPE_PUBLISHABLE_KEY: str = ""

    def stripe_configured(self) -> bool:
        """Whether the Stripe provider can be constructed at all. Deliberately a method and
        not a cached flag: a deployment may inject the secret after import."""
        return bool(self.STRIPE_SECRET_KEY.strip())

    RESEND_API_KEY: str = ""  # blank = welcome emails skipped (logged), registration still works
    # Where the public contact form delivers. SERVER-SIDE ONLY: the browser posts the message
    # and never the destination, so no request can redirect an inquiry to an arbitrary inbox.
    # Deliberately not a VITE_ variable — anything VITE_ is compiled into the JS bundle.
    CONTACT_EMAIL: str = "info@zoikostream.com"
    # ponytail: onboarding@resend.dev only delivers to the Resend account owner. Verify
    # zoikostream.com in Resend and switch this to noreply@zoikostream.com before launch.
    MAIL_FROM: str = "ZoikoStream <onboarding@resend.dev>"

    # "development" | "production". Gates the link-safety assertion in email.py: outside
    # development an emailed link MUST be https and MUST NOT point at localhost, and the
    # send fails loudly rather than delivering an unusable or non-TLS credential-bearing
    # URL (ZST-EC-001 secure-link standard; audit findings F-1/F-2).
    ENVIRONMENT: str = "development"

    # IDN-001 email-verification challenge lifetime. Short by design: the link carries a
    # single-use credential, so minutes rather than days. Surfaced to the recipient in the
    # email preheader and body, so changing it changes the copy automatically.
    EMAIL_VERIFICATION_TTL_MINUTES: int = 30

    def is_production(self) -> bool:
        """Whether this process is a real deployment serving real users.

        A method rather than a cached flag, matching stripe_configured() above: a deployment
        may inject the environment after import.
        """
        return self.ENVIRONMENT.strip().lower() in PRODUCTION_ENVIRONMENTS


settings = Settings()


class InsecureProductionConfig(RuntimeError):
    """A production deployment is missing a secret it cannot safely run without.

    Raised at IMPORT time, so the process dies before it can bind a port and start signing
    tokens. A warning was not enough: the previous behaviour logged and carried on, so a
    deployment that forgot SECRET_KEY came up healthy and served forgeable JWTs — the log line
    scrolled past and nothing else ever objected.
    """


_LOCAL_HOSTS = ("localhost", "127.0.0.1")


def _validate_production_config() -> None:
    """Fail closed on settings whose wrong value is silently exploitable or silently broken.

    Deliberately narrow. Only three things RAISE, each because the default is actively unsafe
    in a deployment and the failure would otherwise surface far from its cause:

      SECRET_KEY    — the default is public, so every JWT would be forgeable.
      CORS_ORIGINS  — the default is localhost, and with allow_credentials that lets any page
                      on a user's own machine call the live API and read the response.
      APP_URL       — the default is http://localhost, which becomes Stripe return URLs the
                      payer cannot reach and credential-bearing email links that email.py
                      then refuses to send.

    Everything else WARNS. Stripe, LiveKit, GCS and Redis each already fail closed at their own
    point of use, and refusing to boot without them would make the API un-deployable for a
    tenant that does not use that feature — or for the deliberate "production infrastructure,
    test payments" stage of a rollout.
    """
    if not settings.is_production():
        if settings.SECRET_KEY.strip() in ("", DEV_SECRET_KEY):
            log.warning(
                "SECRET_KEY is the built-in development default. Every JWT this process issues "
                "can be forged by anyone with the source. Set SECRET_KEY before deploying "
                "(production refuses to start without it)."
            )
        return

    if settings.SECRET_KEY.strip() in ("", DEV_SECRET_KEY):
        raise InsecureProductionConfig(
            f"SECRET_KEY is missing or is the built-in development default while "
            f"ENVIRONMENT={settings.ENVIRONMENT!r}. Every JWT this process would issue could be "
            "forged by anyone with the source. Set SECRET_KEY to a strong random value "
            "(e.g. `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`) and restart."
        )

    # main.py already withholds the localhost ORIGIN REGEX in production, but the allowlist
    # itself defaults to localhost — so gating the regex alone would have left the same hole
    # open by the other route.
    local_origins = [
        o.strip() for o in settings.CORS_ORIGINS.split(",")
        if o.strip() and any(h in o.lower() for h in _LOCAL_HOSTS)
    ]
    if local_origins:
        raise InsecureProductionConfig(
            f"CORS_ORIGINS contains local origins {local_origins} while "
            f"ENVIRONMENT={settings.ENVIRONMENT!r}. Combined with credentialed requests, any page "
            "served from a user's own machine could call this API as them. Set CORS_ORIGINS to "
            "the real browser origin(s) only."
        )

    app_url = settings.APP_URL.strip()
    if not app_url.lower().startswith("https://") or any(h in app_url.lower() for h in _LOCAL_HOSTS):
        raise InsecureProductionConfig(
            f"APP_URL is {app_url!r} while ENVIRONMENT={settings.ENVIRONMENT!r}. It must be the "
            "public https origin: it becomes Stripe checkout return URLs and the base of "
            "credential-bearing email links."
        )

    # Non-fatal: each of these degrades a feature rather than exposing anything.
    if not settings.REDIS_URL.strip():
        log.warning(
            "REDIS_URL is unset in production. The live bus degrades to in-process, so presence "
            "and broadcast state will not be shared between instances — correct only for a "
            "single-instance deployment."
        )
    if not settings.GCS_BUCKET.strip():
        log.warning("GCS_BUCKET is unset in production — recording upload and replay delivery "
                    "will be unavailable.")
    if settings.STRIPE_SECRET_KEY.strip().startswith("sk_test"):
        log.warning(
            "STRIPE_SECRET_KEY is a TEST-mode key in production. Checkout will work end to end "
            "but no real money will move. Intentional for a staged rollout; not for launch."
        )


_validate_production_config()
