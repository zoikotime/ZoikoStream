"""SSE contract: immediate response.started, response.progress stages (C2), heartbeats,
response.error with a stable code, and cancellation on client disconnect followed by a
working retry of the same client_message_id (API §9; ARCH §18, §21.1; API-AT-17)."""
import asyncio
import json
import threading
import time
import uuid

from fastapi import FastAPI
from sqlalchemy import func, select

from app.guide import llm, orchestrator
from app.guide.asgi import MOUNT_PATH, create_app
from app.guide.errors import GuideProblem
from app.guide.llm import ModelResult
from app.guide.models import GuideMessage
from app.guide.settings import get_settings

from conftest import BASE, ORIGIN

ANSWER = ("ZoikoStream Live Events lets organizations stream scheduled live events to invited "
          "audiences.")
QUESTION = "What are live events?"


def _reply():
    return {"answer_text": ANSWER, "answer_class": "factual", "used_source_ids": ["test-product"],
            "action_ids": ["request-demo"], "uncertainty": "none"}


class Gated:
    """A model whose FIRST call blocks until released (or times out); later calls answer at
    once. Lets a test hold a turn in its drafting stage."""
    profile = "gated"

    def __init__(self, hold_seconds=8.0):
        self.calls = 0
        self.release = threading.Event()
        self.entered = threading.Event()
        self.hold_seconds = hold_seconds

    def generate(self, prompt):
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            self.release.wait(self.hold_seconds)
        return ModelResult("ok", _reply(), self.profile)


def _events(text):
    out = []
    for block in text.strip().split("\n\n"):
        if block.startswith(":"):
            out.append((":", None))
            continue
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def _stream(guide, text=QUESTION, cmid=None):
    return guide.post("/messages", {"client_message_id": str(cmid or uuid.uuid4()),
                                    "text": text},
                      headers={"Accept": "text/event-stream"})


def test_started_first_then_progress_stages_then_answer(guide, monkeypatch):
    monkeypatch.setattr(llm, "get_provider", lambda: Gated(hold_seconds=0))
    res = _stream(guide)
    assert res.status_code == 200
    events = _events(res.text)
    names = [n for n, _ in events if n != ":"]
    assert names[0] == "response.started"
    assert events[0][1]["request_id"] == res.headers["x-request-id"]
    stages = [d["stage"] for n, d in events if n == "response.progress"]
    assert stages == ["retrieving", "drafting", "checking"]
    # progress comes right after started and before any answer content
    assert names[1:4] == ["response.progress"] * 3
    assert names[-1] == "response.completed"
    completed = events[-1][1]
    assert uuid.UUID(completed["assistant_message_id"])
    assert completed["session_state"] and completed["grounded"] is True
    deltas = "".join(d["delta"] for n, d in events if n == "response.text.delta")
    assert deltas == ANSWER


def test_failure_after_start_is_one_response_error_with_stable_code(guide, monkeypatch):
    def boom(*a, **k):
        raise GuideProblem("GUIDE_UNAVAILABLE")
    monkeypatch.setattr(orchestrator, "handle_message", boom)
    events = _events(_stream(guide).text)
    assert [n for n, _ in events] == ["response.started", "response.error"]
    assert events[1][1] == {"code": "GUIDE_UNAVAILABLE", "retryable": True}


def test_unexpected_failure_maps_to_internal_error_without_detail(guide, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db host secret-host.internal exploded")
    monkeypatch.setattr(orchestrator, "handle_message", boom)
    res = _stream(guide)
    assert "secret-host" not in res.text
    assert _events(res.text)[-1] == ("response.error",
                                     {"code": "GUIDE_INTERNAL_ERROR", "retryable": False})


def test_heartbeat_comment_while_a_stage_runs_long(guide, monkeypatch):
    monkeypatch.setenv("GUIDE_SSE_HEARTBEAT_SECONDS", "0.1")
    get_settings.cache_clear()
    monkeypatch.setattr(llm, "get_provider", lambda: Gated(hold_seconds=0.8))
    events = _events(_stream(guide).text)
    assert (":", None) in events
    assert events[-1][0] == "response.completed"


def test_http_errors_are_decided_before_the_stream(guide):
    res = guide.post("/messages", {"client_message_id": str(uuid.uuid4()), "text": "x" * 4001},
                     headers={"Accept": "text/event-stream"})
    assert res.status_code == 413 and res.json()["code"] == "GUIDE_PAYLOAD_TOO_LARGE"


# ── Disconnect mid-stream (API-AT-17) ──────────────────────────────────────────────────────

async def _drive_until_progress_then_disconnect(app, path, headers, body):
    """Raw ASGI client: sends the request, reads until the first response.progress event,
    then reports http.disconnect — exactly what a browser abort looks like to the server."""
    sent: list[bytes] = []
    gone = asyncio.Event()
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            sent.append(message.get("body", b""))
            if b"response.progress" in b"".join(sent):
                gone.set()

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
             "client": ("127.0.0.1", 50000), "server": ("testserver", 80)}
    await asyncio.wait_for(app(scope, receive, send), timeout=20)
    return b"".join(sent).decode()


def test_disconnect_cancels_generation_and_retry_answers(guide, db, monkeypatch):
    provider = Gated(hold_seconds=8.0)
    monkeypatch.setattr(llm, "get_provider", lambda: provider)
    host = FastAPI()
    host.mount(MOUNT_PATH, create_app())
    cmid = str(uuid.uuid4())
    body = json.dumps({"client_message_id": cmid, "text": QUESTION}).encode()
    headers = {"content-type": "application/json", "accept": "text/event-stream",
               "origin": ORIGIN, "x-zs-csrf": guide.csrf,
               "cookie": "; ".join(f"{k}={v}" for k, v in guide.c.cookies.items()),
               "content-length": str(len(body))}
    started = time.monotonic()
    text = asyncio.run(_drive_until_progress_then_disconnect(
        host, f"{BASE}/sessions/{guide.session_id}/messages", headers, body))
    assert "response.started" in text and "response.completed" not in text
    # The held model call was abandoned: the stream ended long before the model's 8 s hold.
    assert time.monotonic() - started < 6

    # No orphan effect: the user turn is kept, no assistant answer was stored.
    deadline = time.monotonic() + 5
    while orchestrator._inflight and time.monotonic() < deadline:
        time.sleep(0.05)
    db.expire_all()
    roles = db.scalars(select(GuideMessage.role).where(
        GuideMessage.session_id == uuid.UUID(guide.session_id))).all()
    assert roles == ["user"]

    # Retry with the SAME client_message_id is answered normally (C2).
    provider.release.set()
    retry = guide.post("/messages", {"client_message_id": cmid, "text": QUESTION})
    assert retry.status_code == 200, retry.text
    assert retry.json()["assistant_message"]["text"] == ANSWER
    assert db.scalar(select(func.count()).select_from(GuideMessage)) == 2


def test_pasted_credential_is_answered_as_json_so_rotation_reaches_the_browser(guide,
                                                                               monkeypatch):
    """Security §7 rotation travels on response headers; an SSE stream has already sent its
    headers when the turn runs, so a credential-bearing turn is answered as JSON instead."""
    from app.guide import sessions
    monkeypatch.setattr(llm, "get_provider", lambda: Gated(hold_seconds=0))
    old_csrf = guide.csrf
    res = _stream(guide, text="my password is Hunter2Secret!99 can you help me log in?")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    new_csrf = res.headers.get(sessions.CSRF_ROTATED_HEADER)
    assert new_csrf and new_csrf != old_csrf
    assert "Hunter2Secret" not in res.text
    guide.csrf = new_csrf
    assert _stream(guide).status_code == 200  # the rotated pair keeps the conversation going
