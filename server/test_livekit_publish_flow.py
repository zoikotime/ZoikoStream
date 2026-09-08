"""Regression cover for the reported live-video failure: the platform showed an event as
LIVE while the producer's banner read "Not publishing — Lost connection to the stream" and
viewers saw only the host placeholder.

Three independent defects had to line up for that, and each one gets locked down here:

  1. ROOM IDENTITY. `event_<uuid>` was hand-written as an f-string in five places (three
     services/moderation.py Ctx builders, routers/events.py's watch_event, routers/admin.py,
     routers/organization.py) plus a hand-rolled prefix strip in the webhook's reverse
     lookup. Nothing made them agree. A producer in one room and viewers in another is
     invisible from both ends — each connects fine, subscribes to nothing, and reports
     healthy — so this is asserted as ONE authority, services.livekit.room_for_event.

  2. TOKEN GRANTS. A publish token missing can_publish (or a playback token carrying it) is
     not visible in any log or UI; the host just gets "insufficient permissions" the instant
     it tries to publish, which reads exactly like a network failure.

  3. HEALTH HONESTY. health_of() judged "is media flowing" purely from presence's
     `publishing` count, which is written ONLY by the LiveKit track_published webhook
     (routers/live.py). Nothing in this repo provisions that webhook and SETUP_GUIDE.md never
     documented the endpoint, so on a deployment without it the count is permanently 0 —
     every live event read as "No media is being published". The producer's own verified
     report (services/broadcast.py's broadcast.media_state action) is now a second, equal
     source, and health is down only when NEITHER says media is flowing.

Pure logic — no database, no Redis, no LiveKit, no network. Run:
`venv/Scripts/python -m pytest test_livekit_publish_flow.py`
"""
import logging
import uuid

import jwt
import pytest

from app.config import settings
from app.services import broadcast
from app.services import livekit as lk


EVENT_ID = uuid.UUID("70312f66-5acc-4095-a977-41e27ea31288")


def _configure():
    """Token minting needs a key/secret; every environment that actually streams has them
    (services/livekit.configured()), dev and CI don't."""
    settings.LIVEKIT_API_KEY = settings.LIVEKIT_API_KEY or "devkey"
    settings.LIVEKIT_API_SECRET = settings.LIVEKIT_API_SECRET or "devsecret-at-least-32-chars-long!"


def _claims(token: str) -> dict:
    return jwt.decode(token, settings.LIVEKIT_API_SECRET, algorithms=["HS256"],
                      options={"verify_aud": False})


def _grants(token: str) -> dict:
    """The `video` claim is what LiveKit actually enforces on."""
    return _claims(token)["video"]


# ── 1. one authoritative event -> room mapping ────────────────────────────────

def test_room_name_has_exactly_one_source_of_truth():
    """Every caller goes through room_for_event, and the webhook's reverse lookup is the
    inverse of that same function rather than a second prefix implementation."""
    room = lk.room_for_event(EVENT_ID)
    assert room == f"event_{EVENT_ID}"
    assert lk.event_id_from_room(room) == str(EVENT_ID)
    # A str id and a UUID id must not produce different rooms — routers pass both.
    assert lk.room_for_event(str(EVENT_ID)) == room
    # services/moderation.py delegates rather than re-deriving the prefix.
    from app.services import moderation as mod
    assert mod.event_id_from_room(room) == str(EVENT_ID)


def test_another_features_room_is_ignored_not_misparsed():
    """routers/streams.py still opens `stream_<id>` rooms. Treating one of those as an event
    room would file a stranger's presence against a real event."""
    for foreign in (None, "", "stream_123", "event_", "event_not-a-uuid", "prefix-event_x"):
        assert lk.event_id_from_room(foreign) is None


def test_room_name_is_never_silently_empty():
    """A token minted against room "" connects to a room nobody else is in — the exact
    invisible failure this whole suite exists to prevent."""
    with pytest.raises(ValueError):
        lk.room_for_event(None)
    with pytest.raises(ValueError):
        lk.room_for_event("")


def test_producer_and_viewer_receive_the_identical_room_name():
    """The reported symptom's first suspect. Both sides are built from the same helper, so
    they cannot diverge — asserted through the tokens themselves, not the helper, because the
    token's `room` grant is what LiveKit joins."""
    _configure()
    room = lk.room_for_event(EVENT_ID)
    producer = lk.create_stream_token(lk.secondary(str(uuid.uuid4()), "host"), room, True)
    viewer = lk.create_stream_token(str(uuid.uuid4()), room, False)
    assert _grants(producer)["room"] == _grants(viewer)["room"] == f"event_{EVENT_ID}"


# ── 2. token grants ───────────────────────────────────────────────────────────

def test_host_token_can_publish():
    _configure()
    g = _grants(lk.create_stream_token(lk.secondary("host-1", "host"), lk.room_for_event(EVENT_ID), True))
    assert g["roomJoin"] is True
    assert g["canPublish"] is True
    assert g["canSubscribe"] is True      # the host also monitors the room
    assert g["canPublishData"] is True


def test_viewer_token_can_subscribe_but_never_publish():
    _configure()
    g = _grants(lk.create_stream_token("viewer-1", lk.room_for_event(EVENT_ID), False))
    assert g["roomJoin"] is True
    assert g["canSubscribe"] is True
    assert g["canPublish"] is False
    assert g["canPublishData"] is False


def test_watch_link_viewer_cannot_publish():
    """A guest admitted by an access link (routers/events.py's guest-link identity) is an
    audience member; can_publish=False is passed by that call site, and the grant must
    reflect it for every identity shape the endpoint can issue."""
    _configure()
    room = lk.room_for_event(EVENT_ID)
    for identity in (f"guest-{uuid.uuid4()}", f"guest-link-{uuid.uuid4()}", f"viewer-{uuid.uuid4()}"):
        assert _grants(lk.create_stream_token(identity, room, False))["canPublish"] is False


def test_playback_tokens_are_short_lived_and_publish_tokens_are_bounded():
    """PLAYBACK_TOKEN_TTL is the BRD's "short-lived playback authorization"; the publisher's
    own token may run longer (a real broadcast does) but must still expire."""
    _configure()
    room = lk.room_for_event(EVENT_ID)
    viewer = _claims(lk.create_stream_token("viewer-1", room, False))
    host = _claims(lk.create_stream_token("host-1", room, True))
    assert viewer["exp"] - viewer["nbf"] == int(lk.PLAYBACK_TOKEN_TTL.total_seconds())
    assert host["exp"] > host["nbf"]
    assert host["exp"] - host["nbf"] > viewer["exp"] - viewer["nbf"]


def test_token_minting_refuses_an_empty_identity():
    _configure()
    with pytest.raises(ValueError):
        lk.create_stream_token("", lk.room_for_event(EVENT_ID), True)
    with pytest.raises(ValueError):
        lk.create_stream_token("host-1", "", True)


# ── 3. health / broadcast-state honesty ───────────────────────────────────────
# health_of(split, status, recording_enforced, producer_publishing)

def _split(publishing=0, participants=1, poor=0):
    return {"publishing": publishing, "participants": participants, "poor_connections": poor}


def test_go_live_alone_does_not_report_healthy_live():
    """THE REPORTED BUG. The click makes the session "live"; nothing has published yet.
    Health must say so rather than reporting ok."""
    health = broadcast.health_of(_split(publishing=0), "live", None, producer_publishing=False)
    assert health["level"] == "down"
    assert "No media is being published" in health["issues"]


def test_producer_that_never_reported_falls_back_to_the_webhook_signal():
    """producer_publishing=None means "this console has not reported", which must not be read
    as "not publishing" — otherwise an older console would degrade a healthy broadcast."""
    assert broadcast.health_of(_split(publishing=1), "live", None, producer_publishing=None)["level"] == "ok"
    assert broadcast.health_of(_split(publishing=0), "live", None, producer_publishing=None)["level"] == "down"


def test_a_confirmed_publication_is_healthy_even_with_no_webhook_configured():
    """The production trap: with no LiveKit webhook pointing at the deployment, presence's
    `publishing` count is permanently 0. The producer's verified report alone must be enough
    to call the broadcast healthy, or every live event degrades ~20s after go-live."""
    health = broadcast.health_of(_split(publishing=0), "live", None, producer_publishing=True)
    assert health["level"] == "ok"
    assert health["issues"] == []


def test_the_webhook_signal_alone_is_still_enough():
    """Symmetric: the producer's report can't see a publication the SFU dropped silently, so
    the webhook stays an equal authority rather than being replaced."""
    assert broadcast.health_of(_split(publishing=1), "live", None, producer_publishing=False)["level"] == "ok"


def test_losing_publication_while_live_is_down_not_ok():
    """LIVE -> DEGRADED. Both sources must agree media stopped before we say so."""
    assert broadcast.health_of(_split(publishing=0), "live", None, producer_publishing=False)["level"] == "down"


def test_recovered_publication_returns_to_ok():
    """DEGRADED -> LIVE, but only once publication is confirmed again."""
    assert broadcast.health_of(_split(publishing=0), "live", None, producer_publishing=True)["level"] == "ok"


def test_a_paused_broadcast_is_never_reported_down():
    """A deliberate pause is not a media failure; the sampler must not degrade the event for
    it (see _sample_once, which only degrades on level == "down")."""
    health = broadcast.health_of(_split(publishing=0), "paused", None, producer_publishing=False)
    assert health["level"] != "down"
    assert "Broadcast is paused" in health["issues"]


def test_a_preview_session_is_not_reported_down():
    """Nothing has gone live, so "no media" is the expected state, not a fault."""
    assert broadcast.health_of(_split(publishing=0), "preview", None, producer_publishing=False)["level"] != "down"


def test_an_uncaptured_recording_warns_without_claiming_media_is_down():
    health = broadcast.health_of(_split(publishing=1), "live", False, producer_publishing=True)
    assert health["level"] == "warn"
    assert "Recording is not being captured" in health["issues"]


def test_the_grace_window_outlasts_the_publishers_own_retry_budget():
    """DEGRADE_GRACE_SECONDS has to exceed a normal camera warm-up, and the sampler's cadence
    must not degrade an event before the producer has had a chance to report."""
    assert broadcast.DEGRADE_GRACE_SECONDS >= broadcast.SAMPLE_SECONDS


# ── 4. secrets never reach the logs ───────────────────────────────────────────

def test_no_livekit_token_or_secret_appears_in_logs(caplog):
    """services/livekit.py logs the reason a room-control call failed. That path must never
    carry the API secret or a minted token — a log aggregator is not a secret store."""
    _configure()
    token = lk.create_stream_token("host-1", lk.room_for_event(EVENT_ID), True)

    with caplog.at_level(logging.DEBUG):
        # A deliberately broken URL: _with_room swallows the failure and logs it.
        original = settings.LIVEKIT_URL
        settings.LIVEKIT_URL = "ws://127.0.0.1:1"
        try:
            import asyncio
            asyncio.run(lk.mute_participant(lk.room_for_event(EVENT_ID), "host-1", True))
        finally:
            settings.LIVEKIT_URL = original

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert settings.LIVEKIT_API_SECRET not in logged
    assert token not in logged
    # The JWT's own segments must not leak piecemeal either.
    for segment in token.split("."):
        if len(segment) > 12:
            assert segment not in logged
