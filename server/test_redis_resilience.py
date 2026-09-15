"""Regression cover for the Cloud Run production error:

    redis.exceptions.TimeoutError: Timeout connecting to server
    services/broadcast.py run_sampler -> _sample_once -> bus.presence_all(event_id)
    -> redis.asyncio hgetall(...)

The provider is Upstash over TLS (`rediss://`), reached over the public internet — there is
no VPC connector and no private endpoint in this deployment, so "Redis is unreachable for a
moment" is a normal operating condition rather than an emergency, and the code has to treat
it as one.

Two separate defects met in that traceback, and both are asserted here:

  1. RESILIENCE. `presence_all` raised straight past _sample_once's per-session loop into
     run_sampler's handler, so ONE unreachable read cost every remaining open session its
     tick — not just the session being read.

  2. HONESTY, which is the dangerous half. The naive repair (swallow the error, carry on
     with an empty presence list) is worse than the crash: an empty presence set with
     status=="live" scores `health_of` -> "down" -> mark_degraded(), which durably writes
     Event.status="degraded" into Postgres and tells every console "No media is being
     published". LiveKit publishes media over its own SFU and never consults Redis, so that
     claim would be fabricated from nothing but a Redis timeout. The sampler must reach NO
     verdict — not "down", and equally not mark_recovered's "live" — when the signals it
     judges from could not be read.

Deterministic and offline throughout: the real bc._sample_once is driven against stubbed
seams (mod.tx / bus), following test_analytics_sampler_orphan.py, which is the only way to
script "Redis times out on the second of three sessions" reliably. The two tests that need a
real server are skipped when REDIS_URL is unset, following test_viewer_reactions.py.

Run: `venv/Scripts/python -m pytest test_redis_resilience.py`
"""
import asyncio
import logging
import sys
import types
import uuid

import pytest
from redis import exceptions as rexc

import app.main  # noqa: F401 — import first: app/services/org.py <-> broadcast circular import
from app.models import BroadcastSession, Event
from app.services import broadcast as bc
from app.services import bus


# The exact production exception. `TimeoutError("Timeout connecting to server")` is raised by
# redis/asyncio/connection.py when the CONNECT phase exceeds socket_connect_timeout — a
# different failure from a timed-out command, and the one the traceback showed.
def _connect_timeout():
    return rexc.TimeoutError("Timeout connecting to server")


def _connection_error():
    return rexc.ConnectionError("Error 104 while writing to socket. Connection reset by peer.")


# ── the exception taxonomy ────────────────────────────────────────────────────

def test_the_production_error_is_classified_as_transient():
    """bus.transient_errors() is what every degrade path catches. If the reported exception
    is not in it, none of the handling below ever runs in production."""
    assert isinstance(_connect_timeout(), bus.transient_errors())
    assert isinstance(_connection_error(), bus.transient_errors())


@pytest.mark.parametrize("exc, why", [
    (ValueError("Expecting value: line 1 column 1"), "a corrupt presence record is a defect"),
    (TypeError("string indices must be integers"), "a shape bug is a defect"),
    (KeyError("identity"), "a missing field is a defect"),
    (rexc.ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value"),
     "we asked Redis for something impossible — that is our bug, not the network's"),
])
def test_programming_errors_are_not_treated_as_connectivity_faults(exc, why):
    assert not isinstance(exc, bus.transient_errors()), why


def test_status_of_distinguishes_a_timeout_from_a_refused_connection():
    assert bus.status_of(_connect_timeout()) == bus.REDIS_TIMEOUT
    assert bus.status_of(_connection_error()) == bus.REDIS_UNAVAILABLE


# ── the connection pool ───────────────────────────────────────────────────────

def test_the_pool_is_bounded_and_every_timeout_is_explicit(monkeypatch):
    """Cloud Run runs one uvicorn worker per instance and scales by instance, so the real
    ceiling is (max instances x max_connections) against Upstash's per-plan concurrent
    connection cap. redis-py's own default is 100 per pool, which multiplies into that cap
    at exactly the traffic that makes it hardest to diagnose: new connects stop completing
    and surface as `Timeout connecting to server`, not as a legible "too many clients".
    """
    built = _capture_client_kwargs(monkeypatch)
    assert built["max_connections"] == bus.settings.REDIS_MAX_CONNECTIONS
    assert built["max_connections"] < 100, "must be below redis-py's unreviewed default"
    # Left at None, both of these block a background ticker for redis-py's 5s default.
    assert built["socket_connect_timeout"] == bus.settings.REDIS_CONNECT_TIMEOUT
    assert built["socket_timeout"] == bus.settings.REDIS_SOCKET_TIMEOUT
    assert built["retry_on_timeout"] is True
    assert built["health_check_interval"] == 30
    assert built["decode_responses"] is True


def test_retry_is_bounded_so_an_outage_is_not_amplified(monkeypatch):
    """redis-py's default policy is 10 attempts starting at 10ms. Against a provider that is
    down, every caller on every instance turns that into a hot loop — a retry storm that
    prolongs the outage it is reacting to."""
    built = _capture_client_kwargs(monkeypatch)
    retry = built["retry"]
    assert retry is not None, "the default 10-attempt policy must not be left in place"
    assert retry.get_retries() == bus.settings.REDIS_RETRIES <= 3


def test_one_client_and_one_pool_are_shared_across_calls(monkeypatch):
    """A pool per request or per event is how the connection cap gets breached. Asserted on
    identity: every await of bus.redis() must hand back the SAME client object."""
    monkeypatch.setattr(bus.settings, "REDIS_URL", "redis://stub.invalid:6379/0")
    monkeypatch.setattr(bus, "_redis", None)

    async def scenario():
        return [await bus.redis() for _ in range(5)]

    clients = asyncio.run(scenario())
    assert all(c is clients[0] for c in clients)
    assert clients[0] is not None
    # Built lazily, and only once — the pool is not reconstructed per call.
    assert len({id(c) for c in clients}) == 1


def _capture_client_kwargs(monkeypatch):
    """Build the real client against an unroutable URL (nothing connects at construction
    time — redis-py connects lazily) and report the kwargs it was configured with."""
    monkeypatch.setattr(bus.settings, "REDIS_URL", "redis://stub.invalid:6379/0")
    monkeypatch.setattr(bus, "_redis", None)
    client = asyncio.run(bus.redis())
    pool = client.connection_pool
    kwargs = pool.connection_kwargs
    return {
        "max_connections": pool.max_connections,
        "socket_connect_timeout": kwargs.get("socket_connect_timeout"),
        "socket_timeout": kwargs.get("socket_timeout"),
        "retry_on_timeout": kwargs.get("retry_on_timeout"),
        "health_check_interval": kwargs.get("health_check_interval"),
        "decode_responses": kwargs.get("decode_responses"),
        "retry": kwargs.get("retry"),
    }


# ── the health probe ──────────────────────────────────────────────────────────

def test_ping_reports_a_timeout_without_raising(monkeypatch):
    """The probe a readiness endpoint calls. One that can itself throw is not a probe."""
    monkeypatch.setattr(bus, "redis", _fake_redis(raises=_connect_timeout()))
    assert asyncio.run(bus.ping()) == bus.REDIS_TIMEOUT


def test_ping_reports_a_refused_connection_as_unavailable(monkeypatch):
    monkeypatch.setattr(bus, "redis", _fake_redis(raises=_connection_error()))
    assert asyncio.run(bus.ping()) == bus.REDIS_UNAVAILABLE


def test_ping_reports_available_when_the_server_answers(monkeypatch):
    monkeypatch.setattr(bus, "redis", _fake_redis())
    assert asyncio.run(bus.ping()) == bus.REDIS_AVAILABLE


def test_ping_reports_disabled_rather_than_broken_when_unconfigured(monkeypatch):
    """REDIS_URL unset is a valid single-instance deployment (config.validate warns, it does
    not fail). It must not be reported as a fault, or the readout cries wolf in dev."""
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    monkeypatch.setattr(bus, "_redis", None)
    assert asyncio.run(bus.ping()) == bus.REDIS_DISABLED


def test_the_status_vocabulary_never_leaks_the_endpoint_or_credentials(monkeypatch):
    """Every value bus.ping() can return, checked against a URL containing a realistic
    Upstash host and password. The status is safe to log and safe to serve unauthenticated
    precisely because it is a closed vocabulary — this is what keeps it one."""
    secret = "AX9sAAIncDEyM2FiY2RlZjEyMzQ1Njc4OTBhYmNkZWY"
    url = f"rediss://default:{secret}@apn1-fake-name-12345.upstash.io:6379"
    monkeypatch.setattr(bus.settings, "REDIS_URL", url)

    statuses = {bus.REDIS_AVAILABLE, bus.REDIS_TIMEOUT, bus.REDIS_UNAVAILABLE,
                bus.REDIS_DISABLED}
    for outcome in (None, _connect_timeout(), _connection_error()):
        monkeypatch.setattr(bus, "redis", _fake_redis(raises=outcome))
        status = asyncio.run(bus.ping())
        assert status in statuses
        for leak in (secret, "upstash.io", "apn1-fake-name-12345", "default", "6379", url):
            assert leak not in status
    # And the log-facing classifier, which takes the exception rather than the URL for
    # exactly this reason.
    for exc in (_connect_timeout(), _connection_error()):
        assert secret not in bus.status_of(exc) and "upstash.io" not in bus.status_of(exc)


def test_a_redis_failure_is_logged_without_the_url(monkeypatch, caplog):
    """The whole log line, not just the status field: a credential reaching Cloud Logging is
    a credential leak, and REDIS_URL carries the password inline."""
    secret = "AX9sAAIncDEyM2FiY2RlZjEyMzQ1Njc4OTBhYmNkZWY"
    url = f"rediss://default:{secret}@apn1-fake-name-12345.upstash.io:6379"
    monkeypatch.setattr(bus.settings, "REDIS_URL", url)
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], presence_raises=_connect_timeout())

    with caplog.at_level(logging.WARNING):
        h.run()

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert text, "the outage must be logged at all"
    for leak in (secret, url, "upstash.io", "apn1-fake-name-12345"):
        assert leak not in text
    assert f"redis_status={bus.REDIS_TIMEOUT}" in text


def _fake_redis(raises=None):
    """Replaces bus.redis with one whose ping() either answers or fails as scripted."""
    class _Client:
        async def ping(self):
            if raises is not None:
                raise raises
            return True

    async def _redis():
        return _Client()

    return _redis


# ── health_of: the claim it is allowed to make ────────────────────────────────

def _split(publishing=0, participants=1, viewers=1):
    return {"participants": participants, "waiting": 0, "viewers": viewers, "speakers": 0,
            "hosts": 0, "hands": 0, "poor_connections": 0, "publishing": publishing}


def test_unreadable_signals_report_unknown_not_down():
    """The core honesty assertion. Same inputs that legitimately score "down" when they were
    actually read, but flagged as unread."""
    health = bc.health_of(_split(publishing=0), "live", None, producer_publishing=None,
                          evidence_available=False)
    assert health["level"] == bc.HEALTH_UNKNOWN
    assert health["level"] != "down"
    assert bc.MEDIA_STATE_UNKNOWN in health["issues"]
    assert "No media is being published" not in health["issues"], (
        "a telemetry outage must not be phrased as a claim about the broadcast")
    assert health["evidence_available"] is False


def test_the_same_inputs_still_score_down_when_they_were_actually_read():
    """The guard against over-correction: a genuine "nobody is publishing" reading must
    still degrade, or the fix has simply disabled the check."""
    health = bc.health_of(_split(publishing=0), "live", None, producer_publishing=False)
    assert health["level"] == "down"
    assert "No media is being published" in health["issues"]
    assert health["evidence_available"] is True


def test_unknown_is_not_smuggled_in_as_ok():
    """It must not read as healthy either — that would hide a real outage behind a green
    badge. `unknown` is its own level, distinct from all three existing ones."""
    assert bc.HEALTH_UNKNOWN not in ("ok", "warn", "down")


# ── _sample_once against a failing Redis ──────────────────────────────────────

class _FakeDB:
    """Just enough Session surface for the real write() closure."""

    def __init__(self, existing_events):
        self.existing = {str(e) for e in existing_events}
        self.added = []

    def get(self, model, pk):
        if model is Event:
            return object() if str(pk) in self.existing else None
        if model is BroadcastSession:
            return None
        return None

    def add(self, row):
        self.added.append(row)


class _Harness:
    """Drives the REAL bc._sample_once with mod.tx and the bus replaced.

    `presence_raises` may be a single exception (every session fails), or a dict keyed by
    event id (only those fail) — which is how "session 2 of 3 cannot be read" is scripted.
    """

    def __init__(self, monkeypatch, sessions, presence_raises=None, media_raises=None,
                 people=None, publish_raises=None):
        self.sessions = sessions
        self.presence_raises = presence_raises
        self.media_raises = media_raises
        self.people = people if people is not None else [
            {"identity": "v1", "role": "viewer", "joined_at": 0.0, "publishing": True}]
        self.publish_raises = publish_raises
        self.snapshots = []
        self.degraded = []
        self.recovered = []
        self.published = []
        self.presence_reads = []
        events = [s["event_id"] for s in sessions]

        async def fake_tx(fn):
            name = getattr(fn, "__name__", "")
            if name == "_open_sessions":
                return list(self.sessions)
            if name == "write":
                db = _FakeDB(events)
                out = fn(db)
                self.snapshots.extend(db.added)
                return out
            raise AssertionError(f"unexpected tx callable in this test: {name!r}")

        monkeypatch.setattr(bc.mod, "tx", fake_tx)
        monkeypatch.setattr(bc, "_counts", lambda db, e, o: {
            "messages": 1, "questions": 2, "reactions": 3, "poll_votes": 0, "polls": 0})
        monkeypatch.setattr(bc.bus, "presence_all", self._presence_all)
        monkeypatch.setattr(bc.bus, "state_set", self._state_set)
        monkeypatch.setattr(bc.bus, "publish", self._publish)
        monkeypatch.setattr(bc, "media_publishing_get", self._media)
        monkeypatch.setattr(bc, "mark_degraded", self._mark_degraded)
        monkeypatch.setattr(bc, "mark_recovered", self._mark_recovered)

    def _scripted(self, spec, event_id):
        if spec is None:
            return None
        if isinstance(spec, dict):
            return spec.get(str(event_id))
        return spec

    async def _presence_all(self, event_id):
        self.presence_reads.append(str(event_id))
        exc = self._scripted(self.presence_raises, event_id)
        if exc is not None:
            raise exc
        return list(self.people)

    async def _media(self, event_id):
        exc = self._scripted(self.media_raises, event_id)
        if exc is not None:
            raise exc
        return True

    async def _state_set(self, event_id, patch):
        return {}

    async def _publish(self, event_id, channel, type_, data=None):
        if self.publish_raises is not None:
            raise self.publish_raises
        self.published.append((str(event_id), channel, type_, data))
        return {}

    async def _mark_degraded(self, event_id, org_id, reason):
        self.degraded.append((str(event_id), reason))
        return True

    async def _mark_recovered(self, event_id, org_id):
        self.recovered.append(str(event_id))
        return True

    def run(self):
        return asyncio.run(bc._sample_once())

    def run_one_sampler_pass(self):
        """One iteration of the real run_sampler body, so the publish loop is covered too."""
        async def once():
            for event_id, tick in await bc._sample_once():
                try:
                    await bc.bus.publish(event_id, "analytics", "analytics.tick", tick)
                except bc.bus.transient_errors():
                    pass
        asyncio.run(once())


def _session(event_id, status="live", peak=0, started_at=None):
    return {"event_id": str(event_id), "org_id": str(uuid.uuid4()), "id": str(uuid.uuid4()),
            "status": status, "peak": peak, "started_at": started_at,
            "event_exists": True, "event_status": "live", "event_deleted": False}


def test_a_presence_timeout_does_not_raise_out_of_the_sampler(monkeypatch):
    """THE REPORTED CRASH. _sample_once must return normally."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], presence_raises=_connect_timeout())
    out = h.run()  # must not raise
    assert [e for e, _ in out] == [str(ev)]


def test_a_presence_connection_error_is_handled_the_same_way(monkeypatch):
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], presence_raises=_connection_error())
    out = h.run()
    assert out and out[0][1]["presence_available"] is False


def test_one_redis_failure_does_not_stop_the_other_events_from_sampling(monkeypatch):
    """The blast radius. A valid / unreachable / valid ordering: BOTH readable sessions must
    still be sampled and still get a snapshot row in the SAME tick. Before the fix the
    exception left the loop entirely, so session 3 was never even attempted."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    h = _Harness(monkeypatch, [_session(a), _session(b), _session(c)],
                 presence_raises={str(b): _connect_timeout()})
    out = h.run()

    assert h.presence_reads == [str(a), str(b), str(c)], "every session must be attempted"
    assert sorted(str(s.event_id) for s in h.snapshots) == sorted([str(a), str(c)])
    assert sorted(e for e, _ in out) == sorted([str(a), str(b), str(c)])
    # And the unreachable one is the only one flagged.
    flags = {e: t.get("presence_available") for e, t in out}
    assert flags[str(b)] is False
    assert flags[str(a)] is not False and flags[str(c)] is not False


def test_a_redis_outage_writes_no_snapshot_at_all(monkeypatch):
    """Not a zero row. A fabricated 0 is indistinguishable from a genuinely empty room in the
    retention graph forever — the reading has to be absent, not invented."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], presence_raises=_connect_timeout())
    h.run()
    assert h.snapshots == []


def test_a_redis_outage_never_reports_a_participant_count(monkeypatch):
    """The tick may say "unknown". It may not say "0". useLiveEvent.js merges a tick into the
    previous analytics block, so an omitted field leaves the console's last real numbers
    standing — while a 0 would overwrite them with an invention."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], presence_raises=_connect_timeout())
    tick = h.run()[0][1]

    assert tick["presence_available"] is False
    for fabricated in ("participants", "viewers", "speakers", "hosts", "waiting", "hands",
                       "peak_viewers", "engagement", "avg_watch_seconds", "publishing",
                       "poor_connections"):
        assert fabricated not in tick, f"{fabricated} was invented from an unreadable store"


def test_a_redis_outage_never_claims_livekit_stopped_publishing(monkeypatch):
    """THE DANGEROUS FAILURE. LiveKit carries media over its own SFU and never consults
    Redis, so a Redis timeout is not evidence about publishing. Degrading here would write
    Event.status="degraded" to Postgres and tell every console "No media is being
    published" — a fabricated claim about a stream that is very probably still fine."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], presence_raises=_connect_timeout())
    tick = h.run()[0][1]

    assert h.degraded == [], "media was declared down on the strength of a Redis timeout"
    assert tick["health"]["level"] == bc.HEALTH_UNKNOWN
    assert "No media is being published" not in tick["health"]["issues"]
    assert bc.MEDIA_STATE_UNKNOWN in tick["health"]["issues"]


def test_a_redis_outage_does_not_claim_recovery_either(monkeypatch):
    """The mirror image, and just as wrong: mark_recovered flips Event.status back to "live".
    "We cannot see" is not a verdict in either direction."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], presence_raises=_connect_timeout())
    h.run()
    assert h.recovered == [], "a degraded event was declared healthy while Redis was down"


def test_an_unreadable_producer_report_alone_does_not_degrade(monkeypatch):
    """Presence read fine but the producer's publication report (same Redis-backed state)
    did not, and presence shows nobody publishing. That combination is unreadable evidence,
    not proof of a dead stream."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], media_raises=_connect_timeout(),
                 people=[{"identity": "v1", "role": "viewer", "joined_at": 0.0}])
    tick = h.run()[0][1]

    assert h.degraded == []
    assert tick["health"]["level"] == bc.HEALTH_UNKNOWN
    # The snapshot IS still written: presence was readable, so those counts are real.
    assert len(h.snapshots) == 1


def test_an_unreadable_producer_report_still_scores_ok_when_presence_proves_publishing(monkeypatch):
    """Guard against over-reach in the other direction. presence's webhook-driven
    `publishing` count is real evidence on its own — losing the producer's report does not
    blind us when a track publication is already visible."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], media_raises=_connect_timeout(),
                 people=[{"identity": "v1", "role": "viewer", "joined_at": 0.0,
                          "publishing": True}])
    tick = h.run()[0][1]
    assert tick["health"]["level"] == "ok"
    assert h.degraded == []


def test_a_genuine_media_drop_is_still_degraded_while_redis_is_healthy(monkeypatch):
    """The check the whole fix must not disable: Redis readable, nobody publishing, past the
    grace window -> the event really is degraded and must be marked."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)],
                 people=[{"identity": "v1", "role": "viewer", "joined_at": 0.0}])

    async def _no_producer(event_id):
        return False

    monkeypatch.setattr(bc, "media_publishing_get", _no_producer)
    h.run()
    assert [r for _, r in h.degraded] == ["No media is being published"]


def test_an_unexpected_exception_from_presence_still_surfaces(monkeypatch):
    """Narrow by construction. A corrupt presence record (json.loads -> ValueError) is a
    defect in our own data, and swallowing it would hide it behind a "Redis is flaky" story
    forever."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)],
                 presence_raises=ValueError("Expecting value: line 1 column 1 (char 0)"))
    with pytest.raises(ValueError):
        h.run()


def test_an_undeliverable_tick_does_not_discard_the_others(monkeypatch):
    """Publishing goes through the same Redis, so during an outage every publish fails too.
    run_sampler must survive that and stay alive for the next interval."""
    a, b = uuid.uuid4(), uuid.uuid4()
    h = _Harness(monkeypatch, [_session(a), _session(b)],
                 publish_raises=_connect_timeout())
    h.run_one_sampler_pass()  # must not raise
    assert h.published == []


def test_sampling_resumes_normally_once_redis_comes_back(monkeypatch):
    """Recovery, not just survival. The same sessions sampled across two passes: the first
    with Redis unreachable, the second with it healthy. Nothing may be left latched."""
    ev = uuid.uuid4()
    session = _session(ev)

    outage = _Harness(monkeypatch, [session], presence_raises=_connect_timeout())
    down_tick = outage.run()[0][1]
    assert down_tick["presence_available"] is False
    assert outage.snapshots == []

    healthy = _Harness(monkeypatch, [session])
    up_tick = healthy.run()[0][1]
    assert up_tick.get("presence_available") is not False
    assert up_tick["health"]["level"] == "ok"
    assert len(healthy.snapshots) == 1
    assert up_tick["participants"] == 1


# ── against a real server, when one is configured ─────────────────────────────

@pytest.fixture
def configured_redis_url():
    """The deployment's real REDIS_URL, or a skip. Mirrors test_viewer_reactions.py: the
    in-process tests above cover every code path, these two prove the endpoint in .env is
    actually reachable and speaks the protocol we think it does."""
    url = bus.settings.REDIS_URL
    if not url:
        pytest.skip("REDIS_URL is not configured — the stubbed tests cover the logic")
    return url


@pytest.fixture
def live_event_id(configured_redis_url):
    """A fresh event id, with the bus's cached client reset either side of the test.

    bus._redis is a module-level singleton and its connections belong to the event loop that
    opened them, so a client left behind by a previous asyncio.run() is bound to a loop that
    no longer exists — the "Event loop is closed" trap documented in
    test_broadcast.py::_preview_status, which here surfaced as an SSL write failure during
    teardown rather than in the test body. Resetting before the cleanup run makes it build
    its own client in its own loop.

    presence_clear, NOT state_clear: only presence_clear also drops the reaction tally (see
    test_viewer_reactions.py::event_id), and these keys have no TTL.
    """
    bus._redis = None
    eid = uuid.uuid4()
    yield eid
    bus._redis = None

    async def cleanup():
        await bus.presence_clear(eid)
        await bus.shutdown()

    asyncio.run(cleanup())


def test_the_configured_endpoint_actually_answers(live_event_id):
    """Connectivity, end to end, against whatever REDIS_URL points at. A deployment carrying
    a deleted or stale endpoint fails HERE rather than 15s into the first broadcast."""
    assert asyncio.run(bus.ping()) == bus.REDIS_AVAILABLE


def test_presence_round_trips_through_the_real_store(live_event_id):
    """presence_all is the exact call from the production traceback."""
    async def scenario():
        await bus.presence_upsert(live_event_id, "viewer-a", {"role": "viewer", "name": "Ada"})
        await bus.presence_upsert(live_event_id, "viewer-b", {"role": "host", "name": "Bo"})
        return await bus.presence_all(live_event_id)

    people = asyncio.run(scenario())
    assert {p["identity"] for p in people} == {"viewer-a", "viewer-b"}
    assert {p["role"] for p in people} == {"viewer", "host"}


def test_reactions_and_presence_survive_a_dropped_connection(live_event_id):
    """Reconnect against the real server: the pool is told every connection died, and the
    next call must transparently rebuild one rather than hand out a corpse — the failure
    mode retry_on_timeout + health_check_interval exist to prevent."""
    async def scenario():
        await bus.presence_upsert(live_event_id, "viewer-a", {"role": "viewer"})
        await bus.reaction_tally(live_event_id, "heart")

        client = await bus.redis()
        before = id(client)
        await client.connection_pool.disconnect()  # every socket dropped underneath us

        # Same client, same pool — and it works anyway.
        after = await bus.redis()
        return before, id(after), await bus.presence_all(live_event_id), \
            await bus.reaction_all(live_event_id)

    before, after, people, tally = asyncio.run(scenario())
    assert before == after, "a reconnect must reuse the pool, not build a second one"
    assert [p["identity"] for p in people] == ["viewer-a"]
    assert tally == {"heart": 1}


# ── the second pool: the rate limiter's own client ────────────────────────────
# services/api_usage.py keeps a SEPARATE synchronous client, so every Cloud Run instance
# holds two pools and both count against the provider's concurrent-connection cap.

def test_the_rate_limiter_retries_after_a_transient_outage(monkeypatch):
    """THE LATCH: `_redis_failed` was a permanent boolean, so a single failed connect — a
    cold start during a provider hiccup — dropped that instance to per-process counters for
    the rest of its life. Redis coming back changed nothing, and the DEV-010 shared counter
    was silently un-shared with no way to notice from outside."""
    from app.services import api_usage

    monkeypatch.setattr(api_usage.settings, "REDIS_URL", "redis://stub.invalid:6379/0")
    monkeypatch.setattr(api_usage, "_redis_client", None)
    monkeypatch.setattr(api_usage, "_redis_failed_at", None)

    attempts = []
    clock = [1000.0]
    monkeypatch.setattr(api_usage.time, "monotonic", lambda: clock[0])

    # api_usage calls `redis.Redis.from_url(...)`, so the stub has to carry that shape.
    class _Redis:
        @staticmethod
        def from_url(url, **kwargs):
            attempts.append(kwargs)
            raise rexc.TimeoutError("Timeout connecting to server")

    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=_Redis))

    assert api_usage._client() is None and len(attempts) == 1
    # Inside the cooldown: no second connect. This is what keeps a per-request path from
    # becoming a retry storm against a provider that is already struggling.
    for _ in range(20):
        assert api_usage._client() is None
    assert len(attempts) == 1, "the cooldown must suppress retries, not just slow them"

    # Past the cooldown: it tries again rather than staying latched off forever.
    clock[0] += api_usage._REDIS_RETRY_COOLDOWN_SECONDS + 1
    assert api_usage._client() is None
    assert len(attempts) == 2, "a transient outage must not disable the shared counter for good"


def test_the_rate_limiter_pool_is_bounded_too(monkeypatch):
    """Two pools per instance, so this one's ceiling counts against the same provider cap.
    It needs far fewer connections than the bus — one INCR per refused request."""
    from app.services import api_usage

    monkeypatch.setattr(api_usage.settings, "REDIS_URL", "redis://stub.invalid:6379/0")
    monkeypatch.setattr(api_usage, "_redis_client", None)
    monkeypatch.setattr(api_usage, "_redis_failed_at", None)

    captured = {}

    class _Client:
        def ping(self):
            return True

    class _Redis:
        @staticmethod
        def from_url(url, **kwargs):
            captured.update(kwargs)
            return _Client()

    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=_Redis))

    assert api_usage._client() is not None
    assert 2 <= captured["max_connections"] <= bus.settings.REDIS_MAX_CONNECTIONS
    assert captured["socket_connect_timeout"] == bus.settings.REDIS_CONNECT_TIMEOUT
    assert captured["socket_timeout"] == bus.settings.REDIS_SOCKET_TIMEOUT
    assert captured["decode_responses"] is True


# ── operator notifications (MED-005) ─────────────────────────────────────────

def test_an_unknown_level_notifies_nobody_and_overwrites_no_history():
    """A Redis outage must not mail an operator "Media failed", and must not overwrite the
    last REAL health level on the session with a non-verdict.

    media_comms.record_health already gates on `level not in LEVEL_TO_VARIANT`, so
    HEALTH_UNKNOWN falls through it untouched. That is load-bearing rather than incidental —
    this test is what stops a future contributor from "completing" the mapping by adding an
    `unknown` variant and turning a telemetry outage back into an incident email.
    """
    from app.services import media_comms

    assert bc.HEALTH_UNKNOWN not in media_comms.LEVEL_TO_VARIANT
    assert bc.HEALTH_UNKNOWN not in media_comms.LEVEL_LABELS
    assert bc.HEALTH_UNKNOWN not in media_comms.IMPACT

    class _Session:
        """Fails the test loudly if record_health writes anything at all."""
        health_level = "ok"

        def __setattr__(self, name, value):
            raise AssertionError(f"unknown health wrote {name}={value!r} to the session")

    unknown = bc.health_of(_split(publishing=0), "live", None, evidence_available=False)
    assert media_comms.record_health(None, None, _Session(), unknown) is None


# ── the pub/sub pump ─────────────────────────────────────────────────────────

def test_the_pump_resubscribes_after_a_dropped_connection(monkeypatch):
    """THE SILENT ONE: a dropped connection raised out of _pump and killed the task, but
    bus._pumps still held it — so subscribe() saw the event as already pumped and never
    started a replacement. That event lost cross-worker fan-out on EVERY channel until the
    last local subscriber disconnected, with nothing in the logs after the first error.

    Scripted as: subscribe fails twice, then succeeds and delivers one envelope.
    """
    attempts = []
    delivered = []

    class _PubSub:
        def __init__(self, fail):
            self.fail = fail

        async def subscribe(self, key):
            attempts.append(key)
            if self.fail:
                raise _connection_error()

        async def listen(self):
            yield {"type": "message", "data": '{"channel": "chat", "type": "message.new"}'}
            raise _connect_timeout()  # the socket dies again afterwards

        async def unsubscribe(self, key):
            pass

        async def aclose(self):
            pass

    class _Client:
        def pubsub(self):
            # Fail the first two subscribes, then let it through.
            return _PubSub(fail=len(attempts) < 2)

    async def _redis():
        return _Client()

    monkeypatch.setattr(bus, "redis", _redis)
    monkeypatch.setattr(bus, "_fanout", lambda eid, env: delivered.append(env))
    # Collapse the backoff so the test does not actually wait out ~1s + jitter per attempt.
    monkeypatch.setattr(bus, "PUMP_RETRY_MIN", 0.0)
    monkeypatch.setattr(bus, "PUMP_RETRY_MAX", 0.0)

    async def scenario():
        task = asyncio.create_task(bus._pump("evt-1"))
        for _ in range(200):          # let it fail, back off, and retry
            await asyncio.sleep(0)
            if delivered:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert len(attempts) >= 3, "the pump gave up instead of resubscribing"
    assert delivered, "fan-out never resumed after the connection came back"


def test_the_pump_backoff_is_capped_and_jittered():
    """Every pump on every instance reconnects at once during a provider outage. An
    unjittered retry makes that a thundering herd against an endpoint already in trouble."""
    assert 0 < bus.PUMP_RETRY_MIN <= bus.PUMP_RETRY_MAX
    assert bus.PUMP_RETRY_MAX <= 60, "a capped backoff must still recover in reasonable time"
    import inspect
    source = inspect.getsource(bus._pump)
    assert "random.uniform" in source, "the retry sleep must be jittered"
    assert "min(delay * 2" in source, "the backoff must be capped"


def test_cancelling_the_pump_still_stops_it(monkeypatch):
    """The retry loop must not defeat subscribe()'s teardown: CancelledError is how the last
    subscriber leaving stops the task, and swallowing it would leak a pump per event."""
    class _PubSub:
        async def subscribe(self, key):
            pass

        async def listen(self):
            while True:
                await asyncio.sleep(0.01)
                yield {"type": "subscribe"}

        async def unsubscribe(self, key):
            pass

        async def aclose(self):
            pass

    class _Client:
        def pubsub(self):
            return _PubSub()

    async def _redis():
        return _Client()

    monkeypatch.setattr(bus, "redis", _redis)

    async def scenario():
        task = asyncio.create_task(bus._pump("evt-2"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled() or task.done()

    asyncio.run(scenario())
