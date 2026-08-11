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

    RESEND_API_KEY: str = ""  # blank = welcome emails skipped (logged), registration still works
    # ponytail: onboarding@resend.dev only delivers to the Resend account owner. Verify
    # zoikostream.com in Resend and switch this to noreply@zoikostream.com before launch.
    MAIL_FROM: str = "ZoikoStream <onboarding@resend.dev>"


settings = Settings()

if settings.SECRET_KEY == DEV_SECRET_KEY:
    log.warning(
        "SECRET_KEY is the built-in development default. Every JWT this process issues can "
        "be forged by anyone with the source. Set SECRET_KEY in .env before deploying."
    )
