"""Runs cases through the real Guide API and collects observations.

The Guide is exercised exactly as a browser would use it (session cookie + CSRF + JSON API),
with two passive spies: one counts model calls, one records the retrieval evidence pack. The
spies observe; they never change behavior.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field


def configure_environment(provider: str | None, database_url: str | None) -> None:
    """Must run BEFORE app.guide is imported (settings are read once)."""
    os.environ["GUIDE_ENABLED"] = "true"
    os.environ["GUIDE_ALLOW_MISSING_ORIGIN"] = "true"
    # Evaluation sends many requests from one client; product rate limits are not under test.
    for key in ("GUIDE_RL_BOOTSTRAP_PER_MIN", "GUIDE_RL_SESSION_CREATE_PER_MIN",
                "GUIDE_RL_SESSION_REQUESTS_PER_MIN", "GUIDE_RL_MESSAGES_PER_MIN"):
        os.environ[key] = "100000"
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
            self.rag_release = retrieval.active_release(db)
        finally:
            db.close()

        self.model_calls = 0
        self.last_pack = None
        real_provider = llm.get_provider()
        self.model_profile = real_provider.profile
        harness = self

        class CountingProvider:
            profile = real_provider.profile

            def generate(self, prompt):
                harness.model_calls += 1
                return real_provider.generate(prompt)

        counting = CountingProvider()
        llm.get_provider = lambda: counting  # passive spy
        real_retrieve = retrieval.retrieve

        def spying_retrieve(*args, **kwargs):
            pack = real_retrieve(*args, **kwargs)
            harness.last_pack = pack
            return pack

        orchestrator.retrieval.retrieve = spying_retrieve

    # ── API helpers ────────────────────────────────────────────────────────────────────────
    def _session(self, page_type: str) -> tuple[str, dict]:
        page_id = {"home": "home", "viewer": "viewer", "contact": "contact", "trust": "trust",
                   "privacy": "privacy", "status": "status"}.get(page_type, "other")
        self.client.cookies.clear()
        res = self.client.post(f"{self.base}/sessions", json={
            "client_session_nonce": str(uuid.uuid4()),
            "page_context": {"page_id": page_id, "page_type": page_type,
                             "path": "/" if page_type == "home" else f"/{page_id}"}})
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

    def run_case(self, case, check, known_sources) -> CaseResult:
        from .scorers import TurnObservation
        sid, headers = self._session(case.page)
        failures: list[str] = []
        turns: list[dict] = []
        started = time.monotonic()
        for i, turn in enumerate(case.turns):
            self.model_calls, self.last_pack = 0, None
            res = self.client.post(f"{self.base}/sessions/{sid}/messages", headers=headers,
                                   json={"client_message_id": str(uuid.uuid4()),
                                         "text": turn.text})
            body = res.json() if res.headers.get("content-type", "").startswith(
                "application/json") else {}
            msg = body.get("assistant_message", {})
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
                    "candidates": pack.candidates})
            is_final = i == len(case.turns) - 1
            expect = {**(case.expect if is_final else {}), **turn.expect}
            turn_failures = check(expect, obs, known_sources)
            failures += [f"turn {i + 1}: {x}" for x in turn_failures]
            turns.append({"text": turn.text, "answer_class": obs.answer_class,
                          "state": obs.session_state, "intent": intent, "boundary": boundary,
                          "sources": obs.sources,
                          "actions": [a.get("action_id") for a in obs.actions],
                          "model_calls": obs.model_calls, "retrieval": obs.retrieval,
                          "answer": obs.text, "failures": turn_failures})
            if self.pace and obs.model_calls:
                time.sleep(self.pace)
        # Leave nothing behind: ending a session schedules accelerated purge.
        self.client.post(f"{self.base}/sessions/{sid}/end", headers=headers)
        return CaseResult(case.test_id, case.suite, case.risk, not failures, failures, turns,
                          int((time.monotonic() - started) * 1000))
