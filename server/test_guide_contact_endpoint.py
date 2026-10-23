"""Where the Guide delivers a lead (app/guide/adapters.py).

The lead goes to the platform's own POST /api/contact, served by this same process (main.py
mounts the Guide into the platform app). The default used to be a fixed local address,
http://127.0.0.1:<port>/api/contact, and no fixed port is right everywhere: Cloud Run listens on
$PORT (8080), the VM's port is set by its deploy script, and developers' local ports differ.
A wrong port is silent — the lead is recorded as failed and nobody is told. So an unset
GUIDE_CONTACT_ENDPOINT now means "the port this request arrived on", over loopback.

These run in the main server suite (the Guide's own suite needs pgvector). The last test starts
a real uvicorn server on an OS-chosen loopback port and proves a lead reaches that same server.
"""
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.guide import adapters
from app.guide.adapters import ContactFlowAdapter, own_contact_endpoint
from app.guide.settings import GuideSettings, get_settings


@pytest.fixture
def endpoint_env(monkeypatch):
    """Set (or unset, with None) GUIDE_CONTACT_ENDPOINT for one test, settings cache cleared."""
    def _set(value):
        if value is None:
            monkeypatch.delenv("GUIDE_CONTACT_ENDPOINT", raising=False)
            # The repo-root .env could still name one; the test must see the bare default.
            monkeypatch.setitem(GuideSettings.model_config, "env_file", None)
        else:
            monkeypatch.setenv("GUIDE_CONTACT_ENDPOINT", value)
        get_settings.cache_clear()
    yield _set
    get_settings.cache_clear()


@pytest.fixture
def posts(monkeypatch):
    """Record the adapter's outbound call instead of making it."""
    seen = []

    def fake_post(url, json, headers, timeout):
        seen.append(url)
        return httpx.Response(202, json={"received": True})
    monkeypatch.setattr(adapters.httpx, "post", fake_post)
    return seen


def test_no_port_is_written_into_the_default():
    assert GuideSettings.model_fields["GUIDE_CONTACT_ENDPOINT"].default == ""


@pytest.mark.parametrize("server,expected", [
    (("127.0.0.1", 8080), "http://127.0.0.1:8080/api/contact"),     # Cloud Run ($PORT)
    (("10.8.0.12", 8080), "http://127.0.0.1:8080/api/contact"),     # container's own IP: never used as the host
    (["testserver", 80], "http://127.0.0.1:80/api/contact"),        # list form (Starlette TestClient)
    (("::1", 8001), "http://[::1]:8001/api/contact"),
    (("::ffff:127.0.0.1", 8000), "http://[::1]:8000/api/contact"),  # dual-stack socket
])
def test_the_endpoint_is_this_servers_own_port_over_loopback(server, expected):
    assert own_contact_endpoint(server) == expected


@pytest.mark.parametrize("server", [None, ("/run/zoikostream.sock", None), ()])
def test_no_tcp_port_means_no_derived_endpoint(server):
    assert own_contact_endpoint(server) is None


def test_an_explicit_setting_wins(endpoint_env, posts):
    endpoint_env("https://contact.example.test/api/contact")
    out = ContactFlowAdapter().submit({"x": 1}, visitor_ip=None, server=("127.0.0.1", 8080))
    assert out.outcome == "confirmed"
    assert posts == ["https://contact.example.test/api/contact"]


def test_unset_delivers_to_the_port_the_request_arrived_on(endpoint_env, posts):
    endpoint_env(None)
    assert get_settings().GUIDE_CONTACT_ENDPOINT == ""
    out = ContactFlowAdapter().submit({"x": 1}, visitor_ip=None, server=("172.17.0.3", 8080))
    assert out.outcome == "confirmed"
    assert posts == ["http://127.0.0.1:8080/api/contact"]


def test_unset_without_a_tcp_port_fails_without_sending(endpoint_env, posts):
    endpoint_env(None)
    out = ContactFlowAdapter().submit({"x": 1}, visitor_ip=None, server=None)
    assert (out.outcome, out.reason) == ("failed", "unconfigured")   # failed, so retryable
    assert posts == []


def test_a_lead_reaches_the_same_server_on_whatever_port_it_listens(endpoint_env, monkeypatch):
    endpoint_env(None)
    # conftest swaps httpx.post for a no-network stub for the whole session. Put a real transport
    # back for this test only, and refuse anything but this test's own server — not merely any
    # loopback address: a developer's API may be listening on one.
    own = {}

    def loopback_only_post(url, **kwargs):
        if not url.startswith(f"http://127.0.0.1:{own.get('port')}/"):
            raise AssertionError(f"the lead was sent somewhere else: {url}")
        with httpx.Client() as client:
            return client.post(url, **kwargs)
    monkeypatch.setattr(adapters.httpx, "post", loopback_only_post)

    received = []
    app = FastAPI()

    @app.post("/api/contact")
    async def contact(request: Request):
        received.append((await request.json(), request.headers.get("x-forwarded-for")))
        return JSONResponse({"received": True}, status_code=202)

    @app.post("/lead")
    def lead(request: Request):  # sync, like the Guide's route: the loop stays free for /api/contact
        out = ContactFlowAdapter().submit({"topic": "sales"}, visitor_ip="203.0.113.9",
                                          server=request.scope.get("server"))
        return {"outcome": out.outcome, "reason": out.reason}

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning",
                                           lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, "test server did not start"
            time.sleep(0.05)
        port = own["port"] = server.servers[0].sockets[0].getsockname()[1]  # chosen by the OS

        with httpx.Client() as client:
            res = client.post(f"http://127.0.0.1:{port}/lead", timeout=15)
        assert res.json() == {"outcome": "confirmed", "reason": None}
        assert received == [({"topic": "sales"}, "203.0.113.9")]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
