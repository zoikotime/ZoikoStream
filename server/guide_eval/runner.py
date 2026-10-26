"""Runs cases through the real Guide API and collects observations.

The Guide is exercised exactly as a browser would use it (session cookie + CSRF + JSON API),
with two passive spies: one counts model calls, one records the retrieval evidence pack. The
spies observe; they never change behavior — EXCEPT while a case declares typed `fixtures`
(EVAL §4), which install a deterministic failure or dynamic source for that case only and are
removed afterwards:

  model: timeout|error|refused   the provider returns that typed failure (G8 degraded)
  retrieval: down                retrieval raises (G2 retrieval unavailable, RAG-AT-30)
  features_off: [...]            kill-switched capabilities (G1/G3 paused features)
  rate_limited: true             the messages limit is exhausted (CF §23)
  status: {...} | "unavailable"  the D1 public status feed (G10 dynamic)
  event_context: {...}           D2 claims, signed into the page context (G10 dynamic)
"""
from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field


def configure_environment(provider: str | None, database_url: str | None) -> None:
    """Must run BEFORE app.guide is imported (settings are read once)."""
    os.environ["GUIDE_ENABLED"] = "true"
    os.environ["GUIDE_ALLOW_MISSING_ORIGIN"] = "true"
    # Evaluation sends many requests from one client; product rate limits are not under test.
    for key in ("GUIDE_RL_BOOTSTRAP_PER_MIN", "GUIDE_RL_SESSION_CREATE_PER_MIN",
                "GUIDE_RL_SESSION_REQUESTS_PER_MIN", "GUIDE_RL_MESSAGES_PER_MIN"):
        os.environ[key] = "100000"
    os.environ.setdefault("GUIDE_EVENT_CONTEXT_KEY", "guide-eval-event-context-key-0123456789")
    if provider:
        os.environ["GUIDE_LLM_PROVIDER"] = provider
    if database_url:
        os.environ["GUIDE_DATABASE_URL"] = database_url


@dataclass
class CaseResult:
    case_id: str
    suite: str
    risk: str
    passed: bool
    failures: list[str]
    turns: list[dict] = field(default_factory=list)
    latency_ms: int = 0
    origin: str = "curated"
    repeat: int = 0


class Harness:
    def __init__(self, pace: float = 0.0):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy import select

        from app.guide import llm, orchestrator
        from app.guide.asgi import MOUNT_PATH, create_app
        from app.guide.db import new_session
        from app.guide.models import GuideKnowledgeSource
        from app.guide.knowledge import retrieval

        self.pace = pace
        self.base = MOUNT_PATH
        host = FastAPI()
        host.mount(MOUNT_PATH, create_app())
        self.client = TestClient(host, raise_server_exceptions=False)
        self._new_session = new_session

        db = new_session()
        try:
            self.known_sources = set(db.scalars(select(GuideKnowledgeSource.source_id)).all())
            # Typed dynamic sources (D1 status) are legitimate citations too.
            self.known_sources.add(orchestrator.STATUS_SOURCE["source_id"])
            from app.guide import event_answers  # D2 validated event context
            self.known_sources.add(event_answers.SOURCE["source_id"])
            self.rag_release = retrieval.active_release(db)
        finally:
            db.close()

        self.model_calls = 0
        self.last_pack = None
        self.fixtures: dict = {}
        real_provider = llm.get_provider()
        self.model_profile = real_provider.profile
        harness = self

        class CountingProvider:
            profile = real_provider.profile

            def generate(self, prompt):
                harness.model_calls += 1
                forced = harness.fixtures.get("model")
                if forced:
                    return llm.ModelResult(status=forced, profile=real_provider.profile)
                return real_provider.generate(prompt)

        counting = CountingProvider()
        llm.get_provider = lambda: counting  # passive spy
        real_retrieve = retrieval.retrieve

        def spying_retrieve(*args, **kwargs):
            if harness.fixtures.get("retrieval") == "down":
                raise RuntimeError("guide_eval fixture: retrieval unavailable")
            pack = real_retrieve(*args, **kwargs)
            harness.last_pack = pack
            return pack

        # retrieval.search() resolves `retrieve` through the module, so one patch covers both.
        retrieval.retrieve = spying_retrieve
        orchestrator.retrieval.retrieve = spying_retrieve

    # ── Fixtures ───────────────────────────────────────────────────────────────────────────
    @contextmanager
    def _fixtures(self, fixtures: dict):
        from app.guide import ratelimit, registry, status
        saved = []

        def patch(obj, name, value):
            saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        self.fixtures = fixtures or {}
        # Cases are independent: a forced model/status outage in one case must not leave a
        # process-wide circuit breaker open for the cases after it.
        from app.guide import breaker
        breaker.reset()
        try:
            off =set(self.fixtures.get("features_off") or [])
            if off:
                real = registry.feature_enabled
                patch(registry, "feature_enabled",
                      lambda db, feature: feature not in off and real(db, feature))
            if self.fixtures.get("rate_limited"):
                real_hit = ratelimit.hit

                def limited(key, limit, window=60.0):
                    if key.startswith("messages:"):
                        return ratelimit.Verdict(False, limit, 0, 30)
                    return real_hit(key, limit, window)
                patch(ratelimit, "hit", limited)
            st = self.fixtures.get("status")
            if st is not None:
                parsed = None if st == "unavailable" else status._parse(st)
                patch(status, "current", lambda: parsed)
            yield
        finally:
            for obj, name, value in reversed(saved):
                setattr(obj, name, value)
            self.fixtures = {}

    # ── API helpers ────────────────────────────────────────────────────────────────────────
    def _session(self, page_type: str, event_claims: dict | None = None) -> tuple[str, dict]:
        page_id = {"home": "home", "viewer": "viewer", "contact": "contact", "trust": "trust",
                   "privacy": "privacy", "status": "status"}.get(page_type, "other")
        self.client.cookies.clear()
        ctx = {"page_id": page_id, "page_type": page_type,
               "path": "/" if page_type == "home" else f"/{page_id}"}
        if event_claims:
            from app.guide import event_context
            ctx["public_event_context_token"] = event_context.issue(event_claims)
        res = self.client.post(f"{self.base}/sessions", json={
            "client_session_nonce": str(uuid.uuid4()), "page_context": ctx})
        res.raise_for_status()
        body = res.json()
        return body["session_id"], {"X-ZS-CSRF": body["csrf_token"]}

    def _row_meta(self, message_id: str | None) -> tuple[str | None, str | None]:
        if not message_id:
            return None, None
        from app.guide.models import GuideMessage
        db = self._new_session()
        try:
            row = db.get(GuideMessage, uuid.UUID(message_id))
            return (row.intent_class, row.boundary_state) if row else (None, None)
        finally:
            db.close()

    def run_case(self, case, check, known_sources, repeat: int = 0) -> CaseResult:
        with self._fixtures(case.fixtures):
            return self._run_case(case, check, known_sources, repeat)

    def _run_case(self, case, check, known_sources, repeat: int) -> CaseResult:
        from .claims import score_answer
        from .scorers import TurnObservation
        sid, headers = self._session(case.page, (case.fixtures or {}).get("event_context"))
        failures: list[str] = []
        turns: list[dict] = []
        started = time.monotonic()
        for i, turn in enumerate(case.turns):
            self.model_calls, self.last_pack = 0, None
            res = self.client.post(f"{self.base}/sessions/{sid}/messages", headers=headers,
                                   json={"client_message_id": str(uuid.uuid4()),
                                         "text": turn.text})
            ctype = res.headers.get("content-type", "")
            body = res.json() if "json" in ctype else {}
            msg = body.get("assistant_message", {}) if res.status_code == 200 else {}
            intent, boundary = self._row_meta(msg.get("message_id"))
            pack = self.last_pack
            obs = TurnObservation(
                text=msg.get("text", ""), answer_class=msg.get("answer_class", ""),
                session_state=body.get("session_state", ""), grounded=bool(msg.get("grounded")),
                sources=[s["source_id"] for s in msg.get("sources", [])],
                actions=msg.get("actions", []), intent=intent, boundary=boundary,
                model_calls=self.model_calls, http_status=res.status_code,
                retrieval=None if pack is None else {
                    "answerability": pack.answerability,
                    "evidence": [e.source_id for e in pack.evidence],
                    "candidates": pack.candidates},
                error_code=body.get("code") if res.status_code != 200 else None)
            is_final = i == len(case.turns) - 1
            expect = {**(case.expect if is_final else {}), **turn.expect}
            turn_failures = check(expect, obs, known_sources)
            failures += [f"turn {i + 1}: {x}" for x in turn_failures]
            grounding = None
            if pack is not None and obs.grounded:
                grounding = score_answer(obs.text, obs.sources, [
                    {"source_id": e.source_id, "title": e.title,
                     "heading_path": e.heading_path, "text": e.text} for e in pack.evidence])
            turns.append({"text": turn.text, "answer_class": obs.answer_class,
                          "state": obs.session_state, "intent": intent, "boundary": boundary,
                          "sources": obs.sources,
                          "actions": [a.get("action_id") for a in obs.actions],
                          "model_calls": obs.model_calls, "retrieval": obs.retrieval,
                          "http_status": res.status_code, "grounding": grounding,
                          # Public corpus text the answer was generated from (judge/review).
                          "evidence_text": [e.text[:1500] for e in pack.evidence]
                          if pack is not None else [],
                          "answer": obs.text, "failures": turn_failures})
            if self.pace and obs.model_calls:
                time.sleep(self.pace)
        # Leave nothing behind: ending a session schedules accelerated purge.
        self.client.post(f"{self.base}/sessions/{sid}/end", headers=headers)
        return CaseResult(case.test_id, case.suite, case.risk, not failures, failures, turns,
                          int((time.monotonic() - started) * 1000), case.origin, repeat)
