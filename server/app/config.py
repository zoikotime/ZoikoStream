import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

# Single .env at the repo root (server/app/config.py -> repo root is two levels up).
ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"

# Anyone holding this value can forge a JWT for any user id and role, so it must never
# survive into a deployed environment. Kept as a default (rather than a required field) so
# a fresh clone still boots, but loudly flagged at startup — see the warning below.
DEV_SECRET_KEY = "dev-secret-change-me"


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


settings = Settings()

if settings.SECRET_KEY == DEV_SECRET_KEY:
    log.warning(
        "SECRET_KEY is the built-in development default. Every JWT this process issues can "
        "be forged by anyone with the source. Set SECRET_KEY in .env before deploying."
    )
