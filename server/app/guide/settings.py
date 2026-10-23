"""Guide configuration. Read from the same root .env as the platform, but by its OWN settings
class — importing `app.config` would construct the platform Settings (and require
DATABASE_URL) just to run the Guide, coupling the two trust domains for no benefit.

Every value that the specs call "configuration, not a hard-coded constant" (session lifetimes,
retention, rate limits) lives here so it can change without a redeploy of logic.
"""
from __future__ import annotations

import base64
import binascii
import logging
import re
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"

PRODUCTION_ENVIRONMENTS = ("production", "prod")


class GuideSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV, extra="ignore")

    # Global kill switch (ZST-ARCH-PUB-GUIDE-001 §26, G4). False => bootstrap reports
    # enabled:false and every session route answers 503 GUIDE_UNAVAILABLE.
    GUIDE_ENABLED: bool = False
    GUIDE_DATABASE_URL: str = ""

    # "local" = master key from GUIDE_LOCAL_MASTER_KEY. Development only: production refuses
    # it (see production_problems) because a key in an env file is not a KMS.
    GUIDE_KEY_PROVIDER: str = "local"
    GUIDE_LOCAL_MASTER_KEY: str = ""

    GUIDE_LLM_PROVIDER: str = "mock"
    # Claude via the Anthropic API. .env values are not exported to os.environ, so the key is
    # read here and passed to the SDK; blank falls back to the SDK's own credential resolution.
    ANTHROPIC_API_KEY: str = ""
    GUIDE_LLM_MODEL: str = "claude-opus-5-5"
    # Groq (OpenAI-compatible API).
    GROQ_API_KEY: str = ""
    GUIDE_GROQ_MODEL: str = "openai/gpt-oss-120b"
    GUIDE_LLM_EFFORT: str = "low"
    GUIDE_LLM_TIMEOUT_SECONDS: float = 25.0
    GUIDE_EMBEDDING_PROFILE: str = "fastembed:BAAI/bge-small-en-v1.5"

    # Shared with the viewer page that issues public_event_context_token (HS256).
    GUIDE_EVENT_CONTEXT_KEY: str = ""
    GUIDE_EVENT_CONTEXT_ISSUER: str = "zoikostream-viewer"
    GUIDE_EVENT_CONTEXT_AUDIENCE: str = "zoikostream-guide"
    GUIDE_EVENT_CONTEXT_MAX_TTL_SECONDS: int = 4 * 3600

    # Security/Privacy §7.1 launch baseline.
    GUIDE_SESSION_IDLE_MINUTES: int = 30
    GUIDE_SESSION_ABSOLUTE_HOURS: int = 24

    # Security/Privacy §16 retention baseline (days unless named otherwise).
    GUIDE_RETENTION_SESSION_DAYS: int = 30
    GUIDE_RETENTION_CONVERSATION_DAYS: int = 30
    GUIDE_RETENTION_EVENT_CONTEXT_HOURS: int = 24
    GUIDE_RETENTION_SUPPORT_FLOW_DAYS: int = 30
    GUIDE_RETENTION_LEAD_FAILED_DAYS: int = 30
    GUIDE_RETENTION_HANDOFF_DAYS: int = 30
    GUIDE_RETENTION_FEEDBACK_COMMENT_DAYS: int = 90
    GUIDE_RETENTION_FEEDBACK_DAYS: int = 365
    GUIDE_RETENTION_CONSENT_DAYS: int = 730
    GUIDE_RETENTION_AUDIT_DAYS: int = 365

    # Rate limits (API §15). Illustrative defaults; never echoed to clients beyond the
    # standard RateLimit-* headers.
    GUIDE_RL_BOOTSTRAP_PER_MIN: int = 120
    GUIDE_RL_SESSION_CREATE_PER_MIN: int = 10
    GUIDE_RL_SESSION_REQUESTS_PER_MIN: int = 60
    GUIDE_RL_MESSAGES_PER_MIN: int = 20

    GUIDE_MAX_MESSAGE_CHARS: int = 4000

    # Lead delivery: the platform's existing public contact flow (see adapters.py), served by this
    # same process. Empty (the default): each lead goes over loopback to the port its request
    # arrived on (adapters.own_contact_endpoint), which is right on Cloud Run ($PORT), behind a
    # VM's reverse proxy and on any local port, with no port written down here. Set it only when
    # the contact route is served elsewhere, or the API listens on a Unix socket or terminates
    # TLS itself.
    GUIDE_CONTACT_ENDPOINT: str = ""
    GUIDE_CONTACT_TIMEOUT_SECONDS: float = 10.0
    GUIDE_RL_LEAD_SUBMIT_PER_HOUR: int = 5

    # Browser origins allowed to call mutating routes. Empty => derived from the platform's
    # CORS_ORIGINS / APP_URL so a dev clone works without extra setup.
    GUIDE_ALLOWED_ORIGINS: str = ""
    CORS_ORIGINS: str = ""
    APP_URL: str = ""
    # Non-browser clients (pytest, Postman) send no Origin. Allowed outside production only.
    GUIDE_ALLOW_MISSING_ORIGIN: bool | None = None

    ENVIRONMENT: str = "development"

    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() in PRODUCTION_ENVIRONMENTS

    def allowed_origins(self) -> set[str]:
        raw = self.GUIDE_ALLOWED_ORIGINS or f"{self.CORS_ORIGINS},{self.APP_URL}"
        return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}

    def origin_allowed(self, origin: str) -> bool:
        origin = origin.rstrip("/")
        if origin in self.allowed_origins():
            return True
        # Same dev escape hatch as the platform CORS config (main.py): Vite moves to 5174/5175
        # when 5173 is busy. Never in production, where only the explicit allowlist counts.
        return not self.is_production() and bool(
            re.fullmatch(r"https?://(localhost|127\.0\.0\.1)(:\d+)?", origin))

    def allow_missing_origin(self) -> bool:
        if self.GUIDE_ALLOW_MISSING_ORIGIN is not None:
            return self.GUIDE_ALLOW_MISSING_ORIGIN and not self.is_production()
        return not self.is_production()

    def master_key_bytes(self) -> bytes | None:
        """The local master key, accepted as 64 hex chars or (url-safe) base64 of >= 32 bytes."""
        raw = self.GUIDE_LOCAL_MASTER_KEY.strip()
        if not raw:
            return None
        try:
            key = bytes.fromhex(raw)
        except ValueError:
            try:
                padded = raw + "=" * (-len(raw) % 4)
                key = base64.urlsafe_b64decode(padded.replace("+", "-").replace("/", "_"))
            except (binascii.Error, ValueError):
                return None
        return key if len(key) >= 32 else None

    def production_problems(self) -> list[str]:
        """Reasons the Guide must stay disabled. Not raised: a bad Guide config disables the
        Guide, it never stops the platform booting."""
        problems = []
        if not self.GUIDE_DATABASE_URL:
            problems.append("GUIDE_DATABASE_URL is not set")
        if self.GUIDE_KEY_PROVIDER == "local" and self.master_key_bytes() is None:
            problems.append("GUIDE_LOCAL_MASTER_KEY is missing or shorter than 32 bytes")
        if self.is_production():
            if self.GUIDE_KEY_PROVIDER == "local":
                problems.append("GUIDE_KEY_PROVIDER=local is not permitted in production")
            if "mock" in [p.strip() for p in self.GUIDE_LLM_PROVIDER.split(",")]:
                problems.append("GUIDE_LLM_PROVIDER=mock is not permitted in production")
            if any("localhost" in o or "127.0.0.1" in o for o in self.allowed_origins()):
                problems.append("allowed origins include localhost in production")
        return problems


@lru_cache
def get_settings() -> GuideSettings:
    return GuideSettings()
