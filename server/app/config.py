from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Single .env at the repo root (server/app/config.py -> repo root is two levels up).
ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV, extra="ignore")

    DATABASE_URL: str
    SECRET_KEY: str = "dev-secret-change-me"  # ponytail: dev fallback; set a real one in .env for prod
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

    RESEND_API_KEY: str = ""  # blank = welcome emails skipped (logged), registration still works
    # ponytail: onboarding@resend.dev only delivers to the Resend account owner. Verify
    # zoikostream.com in Resend and switch this to noreply@zoikostream.com before launch.
    MAIL_FROM: str = "ZoikoStream <onboarding@resend.dev>"


settings = Settings()
