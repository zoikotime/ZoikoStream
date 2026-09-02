"""Connect-path resilience for the live-event WebSocket.

THE PRODUCTION BUG these cover: everything from ensure_state() through the opening
moderator/snapshot used to sit OUTSIDE the endpoint's try/except (which only began at the
receive loop). Any failure in that stretch — Redis unreachable, a slow database, LiveKit
token minting, an unserialisable payload — escaped the endpoint, so the browser saw an
ABNORMAL close instead of a deliberate one. That code is not in useEventStream.js's
FATAL_CODES, so the client reconnected forever: the producer console sat on "Offline - 11"
with no snapshot, and because can_host only ever arrives IN that snapshot, the UI fell back
to its unauthorised defaults and told a correctly-assigned host "No host assigned" /
"View only". Observed in the deployed environment, not hypothesised.
"""
import inspect

# app.main FIRST. app/services/org.py does `from .broadcast import engagement_score` while
# broadcast.py transitively imports org.py, so importing app.routers.live directly — which
# pulls in broadcast — hits that cycle and fails COLLECTION with
# "cannot import name 'engagement_score' from partially initialized module". Pre-existing and
# unrelated to what this file tests (test_broadcast.py fails the same way when run on its own
# against unmodified code); importing app.main resolves the order the way production does.
import app.main  # noqa: F401  - import order matters; see above

from app.routers import live


def _connect_region() -> str:
    """The source between accepting the socket and the opening snapshot."""
    src = inspect.getsource(live.live_socket)
    start = src.index("async with bus.subscribe")
    end = src.index("async def writer")
    return src[start:end]


def test_snapshot_send_is_inside_a_try_block():
    """The send that hands the console its can_host must be guarded. Without this the whole
    authorisation state of the console depends on nothing going wrong."""
    region = _connect_region()
    assert "try:" in region, "connect path has no try block at all"
    assert region.index("try:") < region.index("mod.snapshot("), \
        "mod.snapshot() is reached before any try: — an exception there escapes the endpoint"


def test_presence_upsert_is_guarded_too():
    region = _connect_region()
    assert region.index("try:") < region.index("presence_upsert"), \
        "presence_upsert runs outside the guard"


def test_connect_failure_closes_deliberately_rather_than_escaping():
    region = _connect_region()
    assert "_connect_failed" in region, "no deliberate close on connect failure"


def test_connect_failed_uses_a_retryable_code_not_a_fatal_one():
    """1011 is deliberate: a Redis blip SHOULD be retried. What must not happen is an
    abnormal close carrying no information at all. Equally it must not send 1008, which the
    client treats as terminal — a transient outage would then need a manual page reload."""
    src = inspect.getsource(live._connect_failed)
    assert "WS_1011_INTERNAL_ERROR" in src
    assert "WS_1008_POLICY_VIOLATION" not in src


def test_connect_failed_logs_identifiers_but_never_the_token():
    """Inspects the executable body only — the docstring legitimately mentions the token in
    order to state the rule, and matching on that would be checking the wrong thing."""
    src = inspect.getsource(live._connect_failed)
    body = src.split('"""')[2]          # everything after the docstring
    assert "log.exception" in body, "a connect failure must leave a server-side record"
    for secret in ("token", "authorization", "password", "secret"):
        assert secret not in body.lower(), f"connect-failure log may expose {secret}"
