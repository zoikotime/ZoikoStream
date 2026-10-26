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
    GUIDE_KEY_PROVIDER: str = "local"           # local | gcp-kms
    GUIDE_LOCAL_MASTER_KEY: str = ""
    # "gcp-kms": the crypto key resource (projects/…/locations/…/keyRings/…/cryptoKeys/…) and
    # the KMS-encrypted key set (base64; see crypto.py). Previous key sets: comma-separated.
    GUIDE_KMS_KEY_NAME: str = ""
    GUIDE_KMS_KEYSET: str = ""
    GUIDE_KMS_PREVIOUS_KEYSETS: str = ""

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
    # Retrieval store (RAG §7.1/§28): its own connection pool. Blank = GUIDE_DATABASE_URL
    # (same physical database, independent pool budget). Must reach the knowledge registry.
    GUIDE_RAG_DATABASE_URL: str = ""
    # RAG §12.1 fusion profile: "rrf-v1" (launch baseline) or "similarity-v1".
    GUIDE_RAG_FUSION_PROFILE: str = "rrf-v1"
    # RBAC §5-8 content approval: outside production, sources whose approval is still
    # "pending-approval" may be indexed (with a warning). Production always refuses them.
    GUIDE_KB_ALLOW_PENDING_APPROVAL: bool = True
    # Release manifest application_commit (EVAL §2.1). Blank = `git rev-parse HEAD`.
    GUIDE_APP_COMMIT: str = ""
    # Prompt §19.1 / Security §13.1: providers that passed the Guide evaluation suite.
    # In production any GUIDE_LLM_PROVIDER entry outside this list is refused (llm.py).
    GUIDE_APPROVED_PROVIDERS: str = "anthropic"
    # Prompt §19 routing profiles (FAST_QA / DISCOVERY / HIGH_RISK_FACT) as JSON overrides,
    # e.g. {"HIGH_RISK_FACT": {"model": "...", "effort": "medium", "max_tokens": 3000}}.
    # Blank = every profile uses GUIDE_LLM_MODEL / GUIDE_LLM_EFFORT.
    GUIDE_LLM_ROUTING_PROFILES: str = ""
    # Architecture §21.1 / Security §21: cumulative model tokens (input + output) one session
    # may spend; beyond it generated answers stop and deterministic routes remain.
    GUIDE_SESSION_TOKEN_BUDGET: int = 120000
    # Prompt §22-23: governed prompt / policy release ids (prompt_versions/manifest.json).
    # Blank = manifest default. A runtime config row (guide.prompt_version /
    # guide.policy_version) overrides these without a deploy.
    GUIDE_PROMPT_VERSION: str = ""
    GUIDE_POLICY_VERSION: str = ""

    # Shared with the viewer page that issues public_event_context_token (HS256).
    GUIDE_EVENT_CONTEXT_KEY: str = ""
    GUIDE_EVENT_CONTEXT_ISSUER: str = "zoikostream-viewer"
    GUIDE_EVENT_CONTEXT_AUDIENCE: str = "zoikostream-guide"
    GUIDE_EVENT_CONTEXT_MAX_TTL_SECONDS: int = 4 * 3600

    # Security/Privacy §7.1 launch baseline.
    # Capabilities switched off at deploy time: any of leads, viewer_support, generation
    # (comma-separated). Runtime switches live in guide_runtime_config (registry.py).
    GUIDE_DISABLED_FEATURES: str = ""

    # Scheduled reconcile + retention purge thread (scheduler.py); off only for special cases.
    GUIDE_MAINTENANCE_ENABLED: bool = True

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

    # C8: GET /internal/metrics answers only with header X-Guide-Metrics-Token equal to this
    # value (404 otherwise). Empty = metrics endpoint disabled.
    GUIDE_METRICS_TOKEN: str = ""
    # SSE: a comment line is sent when a stage runs this long without an event, so proxies
    # and the widget's inactivity timer do not cut a slow (but healthy) answer.
    GUIDE_SSE_HEARTBEAT_SECONDS: float = 10.0

    # Lead delivery: the platform's existing public contact flow (see adapters.py).
    GUIDE_CONTACT_ENDPOINT: str = "http://127.0.0.1:8000/api/contact"
    # D1: the platform's PUBLIC status feed (GET /api/status). Empty = status unverifiable.
    GUIDE_STATUS_ENDPOINT: str = "http://127.0.0.1:8000/api/status"
    GUIDE_CONTACT_TIMEOUT_SECONDS: float = 10.0
    GUIDE_RL_LEAD_SUBMIT_PER_HOUR: int = 5

    # ── Security/privacy hardening (Agent 1: SEC §7, §16, §21; ARCH §6.1, §12.1, §22) ──
    # Client IP for rate limiting / lead forwarding: 0 = ignore X-Forwarded-For and use the
    # socket peer; N = the Nth entry from the right of X-Forwarded-For (N trusted proxies,
    # e.g. 1 behind a single load balancer). Never trust the left-most, client-written entry.
    GUIDE_TRUSTED_PROXY_HOPS: int = 0
    # Shared rate-limit counters (ARCH §6.1). Blank GUIDE_REDIS_URL falls back to the
    # platform's REDIS_URL; both blank (or Redis down) = per-process in-memory windows.
    GUIDE_REDIS_URL: str = ""
    REDIS_URL: str = ""
    GUIDE_REDIS_TIMEOUT_SECONDS: float = 0.25
    # Appendix B retention object: lead_local_after_confirmed_days, purge_grace_hours.
    GUIDE_RETENTION_LEAD_CONFIRMED_DAYS: int = 7
    GUIDE_RETENTION_ACCELERATED_PURGE_HOURS: int = 24
    GUIDE_PURGE_GRACE_HOURS: int = 24
    # Reconciliation (ARCH §12.1/12.2): in_progress -> unknown after STALE minutes; an
    # `unknown` outcome still unresolved after DEAD_LETTER hours is dead-lettered.
    GUIDE_RECONCILE_STALE_MINUTES: int = 15
    GUIDE_RECONCILE_DEAD_LETTER_HOURS: int = 24
    # Capability rotation after a CSRF failure burst on a valid capability (SEC §7).
    GUIDE_CSRF_FAILURE_LIMIT: int = 5
    GUIDE_CSRF_FAILURE_WINDOW_SECONDS: int = 600
    # Least-privilege principals (ERD §19, RBAC §20). Blank = use GUIDE_DATABASE_URL.
    GUIDE_MIGRATION_DATABASE_URL: str = ""
    GUIDE_MAINTENANCE_DATABASE_URL: str = ""
    GUIDE_INGEST_DATABASE_URL: str = ""
    GUIDE_TEST_DATABASE_URL: str = ""

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

    def disabled_features(self) -> set[str]:
        return {f.strip() for f in self.GUIDE_DISABLED_FEATURES.split(",") if f.strip()}

    def previous_kms_keysets(self) -> list[str]:
        return [k.strip() for k in self.GUIDE_KMS_PREVIOUS_KEYSETS.split(",") if k.strip()]

    def production_problems(self) -> list[str]:
        """Reasons the Guide must stay disabled. Not raised: a bad Guide config disables the
        Guide, it never stops the platform booting."""
        problems = []
        if not self.GUIDE_DATABASE_URL:
            problems.append("GUIDE_DATABASE_URL is not set")
        if self.GUIDE_KEY_PROVIDER not in ("local", "gcp-kms"):
            problems.append(f"GUIDE_KEY_PROVIDER={self.GUIDE_KEY_PROVIDER!r} is not supported")
        if self.GUIDE_KEY_PROVIDER == "local" and self.master_key_bytes() is None:
            problems.append("GUIDE_LOCAL_MASTER_KEY is missing or shorter than 32 bytes")
        if self.GUIDE_KEY_PROVIDER == "gcp-kms" and not (self.GUIDE_KMS_KEY_NAME
                                                        and self.GUIDE_KMS_KEYSET):
            problems.append("GUIDE_KMS_KEY_NAME and GUIDE_KMS_KEYSET are required for gcp-kms")
        if self.is_production():
            if self.GUIDE_KEY_PROVIDER == "local":
                problems.append("GUIDE_KEY_PROVIDER=local is not permitted in production")
            if "mock" in [p.strip() for p in self.GUIDE_LLM_PROVIDER.split(",")]:
                problems.append("GUIDE_LLM_PROVIDER=mock is not permitted in production")
            if any("localhost" in o or "127.0.0.1" in o for o in self.allowed_origins()):
                problems.append("allowed origins include localhost in production")
            # Environment separation (ARCH §14.1, RBAC §18, SP-AT-19/36): production never
            # runs on a test database, test-only embeddings or the no-Origin test escape.
            if self.GUIDE_DATABASE_URL and (
                    self.GUIDE_DATABASE_URL == self.GUIDE_TEST_DATABASE_URL
                    or _looks_like_test_db(self.GUIDE_DATABASE_URL)):
                problems.append("GUIDE_DATABASE_URL points at a test database in production")
            if self.GUIDE_EMBEDDING_PROFILE.startswith("hash:"):
                problems.append("test-only embedding profile is not permitted in production")
            if self.GUIDE_ALLOW_MISSING_ORIGIN:
                problems.append("GUIDE_ALLOW_MISSING_ORIGIN is not permitted in production")
        return problems


def _looks_like_test_db(url: str) -> bool:
    name = url.rsplit("/", 1)[-1].split("?", 1)[0].lower()
    return bool(re.search(r"(^|[_-])(test|tests|testing|pytest)([_-]|\d|$)", name))


@lru_cache
def get_settings() -> GuideSettings:
    return GuideSettings()
