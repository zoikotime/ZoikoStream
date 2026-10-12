"""Operator Guide configuration (root .env, OPGUIDE_* keys).

The model provider falls back to the public Guide's choice so one key serves both products in
development; production can split them by setting OPGUIDE_LLM_PROVIDER explicitly.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class OpGuideSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV, extra="ignore")

    # Platform-wide availability. The Super Admin kill switch is the FeatureFlag row
    # "operator_guide" (policy.py); this env switch exists for deployments without the flag.
    OPGUIDE_ENABLED: bool = True

    OPGUIDE_LLM_PROVIDER: str = ""          # "" -> GUIDE_LLM_PROVIDER -> "mock"
    GUIDE_LLM_PROVIDER: str = "mock"
    GROQ_API_KEY: str = ""
    OPGUIDE_GROQ_MODEL: str = "openai/gpt-oss-120b"
    ANTHROPIC_API_KEY: str = ""
    OPGUIDE_ANTHROPIC_MODEL: str = "claude-opus-5-5"
    OPGUIDE_LLM_TIMEOUT_SECONDS: float = 20.0

    # Retention (§16: "no unlimited default").
    OPGUIDE_CONVERSATION_RETENTION_DAYS: int = 90
    OPGUIDE_AUDIT_RETENTION_DAYS: int = 365

    # Governed actions.
    OPGUIDE_PROPOSAL_TTL_MINUTES: int = 10
    OPGUIDE_ACTION_TIMEOUT_SECONDS: float = 15.0

    # Abuse/cost control per signed-in user.
    OPGUIDE_MESSAGES_PER_MINUTE: int = 30

    # Freshness thresholds (seconds) after which operational data is labelled stale (§15).
    OPGUIDE_TELEMETRY_STALE_SECONDS: int = 60
    OPGUIDE_STATE_STALE_SECONDS: int = 300

    def llm_provider(self) -> str:
        return (self.OPGUIDE_LLM_PROVIDER or self.GUIDE_LLM_PROVIDER or "mock").strip().lower()


@lru_cache
def get_settings() -> OpGuideSettings:
    return OpGuideSettings()
