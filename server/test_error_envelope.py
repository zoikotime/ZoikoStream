"""An unhandled server error must still reach the browser.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
Starlette answers an unhandled exception from `ServerErrorMiddleware`, which wraps every
middleware the app adds — so that 500 never passes back out through `CORSMiddleware` and
carries no `Access-Control-Allow-Origin`. A browser will not hand such a response to
JavaScript, so the fetch rejects with no response attached and axios reports the string
`Network Error`.

That is the wrong layer. The request arrived, the server answered, and the answer was thrown
away at the edge — but the console says the API is unreachable, which sends whoever is
debugging it to the proxy, the deployment and the network before the traceback. On the
Billing page it was worse than misleading: the message is interpolated into
"Current subscription detected. {error} Plan changes are paused", so a single discarded
response told an operator their subscription was in trouble.

`app.exception_handler(Exception)` cannot fix this — FastAPI routes that one to
ServerErrorMiddleware, the very layer outside CORS. It has to be middleware declared before
CORS is added, so that it ends up inside it. These tests pin both halves: the envelope, and
the ordering that makes the envelope reachable.
"""
import pytest
from fastapi.testclient import TestClient

from app import main as main_app

PROBE = "/api/__unhandled_error_probe__"
ORIGIN = "http://localhost:5173"


@pytest.fixture
def client():
    """A route that raises, mounted on the REAL app so the real middleware stack answers it.

    `raise_server_exceptions=False` makes TestClient behave like a browser — it returns the
    response the server produced instead of re-raising the exception in the test process,
    which is the only way to inspect what a browser would actually receive.
    """
    async def _boom():
        raise RuntimeError("deliberate fault for the error-envelope test")

    main_app.app.add_api_route(PROBE, _boom, methods=["GET"])
    # Moved to the FRONT of the table. The app ends with a catch-all that serves the SPA, and
    # routes match in order — appended, this probe sits behind it and never runs.
    main_app.app.router.routes.insert(0, main_app.app.router.routes.pop())
    try:
        # No `with`: the lifespan starts background tickers and opens a database connection,
        # and nothing here needs either.
        yield TestClient(main_app.app, raise_server_exceptions=False)
    finally:
        main_app.app.router.routes = [
            r for r in main_app.app.router.routes if getattr(r, "path", None) != PROBE
        ]


# ── the envelope ────────────────────────────────────────────────────────────────────────

def test_an_unhandled_error_answers_500_rather_than_dropping_the_connection(client):
    assert client.get(PROBE).status_code == 500


def test_the_error_response_carries_cors_headers(client):
    """The whole point. Without this header the browser discards the response and the page
    reports a network failure for a request the server definitely answered."""
    r = client.get(PROBE, headers={"Origin": ORIGIN})
    assert r.headers.get("access-control-allow-origin") == ORIGIN


def test_the_body_is_json_with_a_readable_detail(client):
    """errMsg reads `detail`. An HTML error page renders as the generic fallback, which is the
    same dead end in a different costume.

    The shape is the project's STRUCTURED one — {"code", "message"} — which api.js already
    understands: errMsg returns `detail.message`, and errCode branches on `detail.code`."""
    detail = client.get(PROBE, headers={"Origin": ORIGIN}).json().get("detail")
    assert isinstance(detail, dict), f"expected a structured detail, got {type(detail).__name__}"
    assert isinstance(detail.get("message"), str) and detail["message"].strip()
    assert detail.get("code")


def test_the_detail_does_not_leak_the_exception(client):
    """This reaches an unauthenticated browser. An exception message can carry a query, a
    path, or a payment provider's own reply."""
    body = client.get(PROBE, headers={"Origin": ORIGIN}).json()
    assert "deliberate fault" not in body["detail"]
    assert "RuntimeError" not in body["detail"]
    assert "Traceback" not in body["detail"]


def test_the_exception_still_propagates_so_nothing_hides_it(client):  # noqa: ARG001
    """The response is sent, and THEN the exception is re-raised.

    `client` is requested for its fixture side effect: it is what mounts the probe route.

    That is what keeps error visibility unchanged: uvicorn still logs the traceback,
    ServerErrorMiddleware sees a response already started and does not write a second one, and
    a client configured to surface server exceptions still sees the real one. A handler that
    swallowed the exception instead would turn every unhandled fault into a silent 500."""
    raising = TestClient(main_app.app, raise_server_exceptions=True)
    with pytest.raises(RuntimeError, match="deliberate fault"):
        raising.get(PROBE)


# ── the ordering that makes it work ─────────────────────────────────────────────────────

def test_the_handler_sits_inside_the_cors_middleware():
    """Structural, because the ordering is the entire mechanism and it is decided by the
    order of two calls hundreds of lines apart. `add_middleware` prepends, so the FIRST entry
    is outermost: the handler must appear AFTER CORSMiddleware in this list to be inside it.
    """
    names = []
    for m in main_app.app.user_middleware:
        dispatch = m.kwargs.get("dispatch") if getattr(m, "kwargs", None) else None
        names.append(getattr(dispatch, "__name__", None) or getattr(m.cls, "__name__", ""))
    assert "ReadableServerErrors" in names, "the handler is not installed at all"
    assert "CORSMiddleware" in names
    assert names.index("ReadableServerErrors") > names.index("CORSMiddleware"), \
        "the handler is outside CORSMiddleware, so its response loses the CORS headers"


# ── what must NOT have changed ──────────────────────────────────────────────────────────

def test_an_ordinary_request_is_untouched(client):
    """The catch-all must not sit in front of normal traffic doing anything."""
    r = client.post("/api/contact", json={
        # example.com, not a .invalid placeholder: EmailStr runs email_validator, which
        # refuses special-use domains outright.
        "first": "Ada", "last": "Lovelace", "email": "ada@example.com",
        "org": "", "country": "United Kingdom", "topic": "Other", "message": "Hello",
    })
    assert r.status_code == 202


def test_a_validation_error_is_still_a_422(client):
    """HTTPException and RequestValidationError are handled further in, by ExceptionMiddleware,
    so they must never reach the catch-all and become an opaque 500."""
    r = client.post("/api/contact", json={"first": "Ada"})
    assert r.status_code == 422


def test_a_missing_route_is_still_a_404(client):
    assert client.get("/api/__definitely_not_a_route__").status_code == 404
