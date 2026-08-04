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

    # LiveKit — ponytail: default "" so the app still boots without them; streaming
    # (services/livekit.py, /streams) needs real values, so set these in .env before using it.
    LIVEKIT_URL: str = ""
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""

    # Redis — ponytail: blank = single-process fan-out only (fine for dev and one uvicorn
    # worker). Set it to share live-event traffic + presence across workers/hosts.
    REDIS_URL: str = ""

    # Object storage for recordings (S3-compatible: AWS S3, Cloudflare R2, MinIO, Wasabi…).
    # ponytail: blank = no bucket, which is exactly today's behaviour — LiveKit egress has
    # nowhere to put a file, so a recording is marked `enforced=False` and the console says the
    # capture was not retained instead of offering a download that 404s. Set these and the same
    # code path starts producing real files and signed URLs; nothing above services/storage.py
    # changes. S3_ENDPOINT is only needed for non-AWS providers.
    S3_BUCKET: str = ""
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_ENDPOINT: str = ""          # e.g. https://<account>.r2.cloudflarestorage.com
    S3_FORCE_PATH_STYLE: bool = False   # MinIO and most self-hosted gateways need True
    # Seconds a download/playback URL stays valid. Short by design: the URL is the credential,
    # so it must expire faster than it can be pasted into a group chat and reused for a week.
    SIGNED_URL_TTL: int = 900

    RESEND_API_KEY: str = ""  # blank = welcome emails skipped (logged), registration still works
    # Svix signing secret for Resend's delivery webhooks ("whsec_..."). Blank = the webhook
    # endpoint returns 503 rather than trusting an unsigned body, and invitations stay at
    # "sent" instead of claiming a delivery nobody confirmed.
    RESEND_WEBHOOK_SECRET: str = ""
    # ponytail: onboarding@resend.dev only delivers to the Resend account owner. Verify
    # zoikostream.com in Resend and switch this to noreply@zoikostream.com before launch.
    MAIL_FROM: str = "ZoikoStream <onboarding@resend.dev>"


settings = Settings()

if settings.SECRET_KEY == DEV_SECRET_KEY:
    log.warning(
        "SECRET_KEY is the built-in development default. Every JWT this process issues can "
        "be forged by anyone with the source. Set SECRET_KEY in .env before deploying."
    )
