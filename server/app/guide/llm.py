"""Model gateway (ZST-ARCH-PUB-GUIDE-001 §7.1).

Provider-neutral: the orchestrator sees only `generate(prompt) -> ModelResult`. Timeout,
output budget and retry policy live here, the provider key never leaves the server, and a model
response is candidate data — it executes nothing (the validator decides what is shown).

Providers:
  * "anthropic" — Claude (GUIDE_LLM_MODEL, default claude-opus-5-5) via the Anthropic SDK.
    Structured output constrains the reply to the per-turn JSON schema. Effort defaults to
    "low": answers are short and grounded, and first-response latency matters (PRD §18).
    No server-side model fallback: only the configured, evaluated model answers (SP-AT-20).
  * "groq" — Groq's OpenAI-compatible API (GUIDE_GROQ_MODEL, default openai/gpt-oss-120b),
    strict JSON-schema output, low reasoning effort.
  * "mock" — deterministic, offline; answers from the top evidence unit. Development and tests.

A comma-separated value is an ordered fallback chain (FallbackProvider): with
GUIDE_LLM_PROVIDER=anthropic,groq Claude answers, and Groq is used only when Claude errors,
times out or truncates.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache

from .prompts import TurnPrompt
from .settings import get_settings

log = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 4000  # covers adaptive thinking at low effort + a short JSON answer


@dataclass
class ModelResult:
    status: str                    # ok | refused | error | timeout | truncated
    data: dict | None = None
    profile: str = ""
    latency_ms: int = 0
    usage: dict = field(default_factory=dict)


class MockProvider:
    profile = "mock"

    # Words that say nothing about WHAT is asked; overlap on these is not relevance.
    _STOP = {"what", "which", "when", "where", "does", "have", "with", "your", "about", "there",
             "this", "that", "from", "will", "would", "could", "should", "zoikostream", "tell",
             "please", "they", "them", "their", "into", "after", "before", "show", "much",
             "many", "like", "just", "being", "make", "able", "used", "using", "anyone"}

    def _terms(self, text: str) -> set[str]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        return {w[:6] for w in words if len(w) > 3 and w not in self._STOP}

    def generate(self, prompt: TurnPrompt) -> ModelResult:
        started = time.monotonic()
        user = prompt.messages[-1]["content"]
        found = re.findall(r'<source id="([^"]+)"[^>]*>\n(.*?)\n  </source>', user, re.S)
        question = re.search(r"<user_message>\n(.*?)\n</user_message>", user, re.S)
        qterms = self._terms(question.group(1) if question else "")
        # Crude stand-in for the model's relevance judgment: answer from the unit sharing the
        # most content words with the question; no shared content word means a gap.
        scored = sorted(((len(qterms & self._terms(body)), i, sid, body)
                         for i, (sid, body) in enumerate(found)), key=lambda t: (-t[0], t[1]))
        sources = ([(sid, body) for overlap, _i, sid, body in scored if overlap > 0]
                   if qterms else found)  # "What is ZoikoStream?" has no content words
        if not sources:
            data = {"answer_text": "I don't have an approved ZoikoStream source that confirms "
                                   "that information.",
                    "answer_class": "knowledge_gap", "used_source_ids": [],
                    "action_ids": prompt.allowed_action_ids[:1], "uncertainty": "gap"}
        else:
            sid, body = sources[0]
            body = re.sub(r"^Q: .*?\nA: ", "", body.strip(), flags=re.S)
            sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", body))
            data = {"answer_text": " ".join(sentences[:2]).replace("&amp;", "&"),
                    "answer_class": "factual", "used_source_ids": [sid],
                    "action_ids": prompt.allowed_action_ids[:1], "uncertainty": "none"}
        return ModelResult("ok", data, self.profile,
                           int((time.monotonic() - started) * 1000))


class AnthropicProvider:
    def __init__(self):
        import anthropic  # deferred: the platform never needs it unless the Guide uses Claude
        s = get_settings()
        self._anthropic = anthropic
        kwargs = {"api_key": s.ANTHROPIC_API_KEY} if s.ANTHROPIC_API_KEY else {}
        self._client = anthropic.Anthropic(timeout=s.GUIDE_LLM_TIMEOUT_SECONDS, max_retries=1,
                                           **kwargs)
        self.model = s.GUIDE_LLM_MODEL
        self.effort = s.GUIDE_LLM_EFFORT
        self.profile = f"{self.model}:{self.effort}"

    def generate(self, prompt: TurnPrompt) -> ModelResult:
        a = self._anthropic
        started = time.monotonic()
        try:
            # No server-side model fallback: only the configured, evaluated model answers
            # (Security §13.1, SP-AT-20). Our own provider chain is the only fallback.
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                # Stable system prompt first so it can be cached across turns.
                system=[{"type": "text", "text": prompt.system,
                         "cache_control": {"type": "ephemeral"}}],
                messages=prompt.messages,
                output_config={"effort": self.effort,
                               "format": {"type": "json_schema", "schema": prompt.schema}},
            )
        except a.APITimeoutError:
            return ModelResult("timeout", profile=self.profile, latency_ms=self._ms(started))
        except a.RateLimitError:
            log.warning("guide model rate limited")
            return ModelResult("error", profile=self.profile, latency_ms=self._ms(started))
        except a.APIStatusError as exc:
            log.error("guide model API error status=%s request_id=%s", exc.status_code,
                      getattr(exc, "request_id", None))
            return ModelResult("error", profile=self.profile, latency_ms=self._ms(started))
        except a.APIConnectionError:
            log.error("guide model connection error")
            return ModelResult("error", profile=self.profile, latency_ms=self._ms(started))

        usage = {"input_tokens": response.usage.input_tokens,
                 "output_tokens": response.usage.output_tokens,
                 "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens",
                                                    None)}
        if response.stop_reason == "refusal":
            return ModelResult("refused", profile=self.profile, latency_ms=self._ms(started),
                               usage=usage)
        if response.stop_reason == "max_tokens":
            return ModelResult("truncated", profile=self.profile, latency_ms=self._ms(started),
                               usage=usage)
        text = next((b.text for b in response.content if b.type == "text"), None)
        try:
            data = json.loads(text) if text else None
        except json.JSONDecodeError:
            data = None
        return ModelResult("ok" if isinstance(data, dict) else "error", data,
                           f"{response.model}:{self.effort}", self._ms(started), usage)

    @staticmethod
    def _ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)


class GroqProvider:
    """Groq's OpenAI-compatible chat API (default model openai/gpt-oss-120b).

    Same contract as the other providers: the per-turn JSON schema is enforced with strict
    structured output, reasoning effort is kept low for latency, and the key never leaves the
    server. Called with plain httpx (already a dependency) rather than another SDK."""

    URL = "https://api.groq.com/openai/v1/chat/completions"
    # Groq counts the completion budget against the tokens-per-minute limit, so it is sized
    # to what a short grounded JSON answer at low reasoning effort actually needs.
    MAX_COMPLETION_TOKENS = 1024
    # A 429 with a short Retry-After is retried once — before anything is shown, so it can
    # never duplicate a visible answer (Architecture §18). Longer waits degrade immediately.
    MAX_RETRY_WAIT_SECONDS = 4.0

    def __init__(self):
        s = get_settings()
        if not s.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY is not configured")
        self._key = s.GROQ_API_KEY
        self.model = s.GUIDE_GROQ_MODEL
        self.timeout = s.GUIDE_LLM_TIMEOUT_SECONDS
        self.profile = f"groq:{self.model}"

    def generate(self, prompt: TurnPrompt) -> ModelResult:
        import httpx
        started = time.monotonic()
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": prompt.system}, *prompt.messages],
            "temperature": 0.2,
            "max_completion_tokens": self.MAX_COMPLETION_TOKENS,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "guide_answer", "strict": True, "schema": prompt.schema}},
        }
        if self.model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"
        for attempt in range(2):
            try:
                res = httpx.post(self.URL, json=body, timeout=self.timeout,
                                 headers={"Authorization": f"Bearer {self._key}"})
            except httpx.TimeoutException:
                return ModelResult("timeout", profile=self.profile,
                                   latency_ms=self._ms(started))
            except httpx.HTTPError as exc:
                log.error("guide model connection error: %s", type(exc).__name__)
                return ModelResult("error", profile=self.profile, latency_ms=self._ms(started))
            if res.status_code != 429 or attempt:
                break
            wait = _retry_after(res)
            if wait is None or wait > self.MAX_RETRY_WAIT_SECONDS:
                break
            time.sleep(wait)
        if res.status_code == 429:
            log.warning("guide model rate limited (retry-after=%s)", res.headers.get("retry-after"))
            return ModelResult("error", profile=self.profile, latency_ms=self._ms(started))
        if res.status_code != 200:
            # Body is not logged: it can echo request content.
            log.error("guide model API error status=%s", res.status_code)
            return ModelResult("error", profile=self.profile, latency_ms=self._ms(started))
        payload = res.json()
        choice = (payload.get("choices") or [{}])[0]
        usage = payload.get("usage") or {}
        usage = {"input_tokens": usage.get("prompt_tokens"),
                 "output_tokens": usage.get("completion_tokens")}
        if choice.get("finish_reason") == "length":
            return ModelResult("truncated", profile=self.profile, latency_ms=self._ms(started),
                               usage=usage)
        content = (choice.get("message") or {}).get("content")
        try:
            data = json.loads(content) if content else None
        except json.JSONDecodeError:
            data = None
        return ModelResult("ok" if isinstance(data, dict) else "error", data, self.profile,
                           self._ms(started), usage)

    @staticmethod
    def _ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)


def _retry_after(res) -> float | None:
    try:
        return float(res.headers.get("retry-after", ""))
    except ValueError:
        return None


class FallbackProvider:
    """Ordered provider chain, e.g. GUIDE_LLM_PROVIDER=anthropic,groq.

    The next provider is tried only when the current one could not answer — error, timeout or
    truncated output. A "refused" result is final: re-asking a second model after the first
    declined would turn the fallback into a way around that decision. Nothing has been shown
    to the user before a fallback, so it can never duplicate a visible answer.
    """
    RETRYABLE = ("error", "timeout", "truncated")

    def __init__(self, providers: list):
        self.providers = providers
        self.profile = ">".join(p.profile for p in providers)

    def generate(self, prompt: TurnPrompt) -> ModelResult:
        total_ms, result = 0, None
        for i, provider in enumerate(self.providers):
            try:
                result = provider.generate(prompt)
            except Exception:  # an unexpected provider bug must not skip the fallback
                log.exception("guide model %s raised", provider.profile)
                result = ModelResult("error", profile=provider.profile)
            total_ms += result.latency_ms
            if result.status not in self.RETRYABLE:
                break
            if i + 1 < len(self.providers):
                log.warning("guide model %s returned %s; falling back to %s", provider.profile,
                            result.status, self.providers[i + 1].profile)
        result.latency_ms = total_ms
        return result


_PROVIDERS = {"anthropic": lambda: AnthropicProvider(), "groq": lambda: GroqProvider(),
              "mock": lambda: MockProvider()}


@lru_cache
def get_provider():
    names = [n.strip().lower() for n in get_settings().GUIDE_LLM_PROVIDER.split(",") if n.strip()]
    unknown = [n for n in names if n not in _PROVIDERS]
    if not names or unknown:
        raise ValueError(f"unknown GUIDE_LLM_PROVIDER {get_settings().GUIDE_LLM_PROVIDER!r}")
    providers = []
    for name in names:
        try:
            providers.append(_PROVIDERS[name]())
        except Exception as exc:  # e.g. a missing key: skip it, keep the rest of the chain
            log.error("guide model provider %s unavailable: %s", name, type(exc).__name__)
    if not providers:
        raise ValueError("no configured GUIDE_LLM_PROVIDER is available")
    return providers[0] if len(providers) == 1 else FallbackProvider(providers)
