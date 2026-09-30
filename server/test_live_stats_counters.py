"""Live Broadcast Stats: the interaction tiles must follow the room, not the connect moment.

The bug: the host's Stats tab showed Questions / Reactions / Poll votes as 0 for the whole
broadcast even after a viewer voted. analytics_now() fills those tiles in the socket's
OPENING snapshot, but the sampler's 15s analytics.tick carried only `engagement` — and
useLiveEvent.js merges a tick OVER the previous block, so any field the tick omits keeps
its connect-time value forever. Engagement (computed from the same fresh counts) moved;
the tiles next to it did not.

Offline and deterministic: the real bc._sample_once runs against test_redis_resilience's
harness (stubbed tx, bus and LiveKit state), so no database row or Redis key is touched.
"""
import uuid

import app.main  # noqa: F401  - import order matters; see test_viewer_reactions.py

from app.services import broadcast as bc
from test_redis_resilience import _Harness, _connect_timeout, _session

VIEWER = {"identity": "v1", "role": "viewer", "joined_at": 0.0, "publishing": True}
COUNTER_FIELDS = {"questions_asked", "reactions", "poll_votes", "poll_participation"}


def _counts(**over):
    base = {"messages": 0, "questions": 0, "reactions": 0, "poll_votes": 0, "polls": 0}
    return {**base, **over}


def _tick(monkeypatch, counts, people=(VIEWER,), peak=0):
    h = _Harness(monkeypatch, [_session(uuid.uuid4(), peak=peak)], people=list(people))
    monkeypatch.setattr(bc, "_counts", lambda db, e, o: counts)
    return h.run()[0][1]


def test_the_tick_carries_the_counter_tiles(monkeypatch):
    """THE REPORTED CASE: one viewer, one poll, one vote."""
    tick = _tick(monkeypatch, _counts(poll_votes=1, questions=2, reactions=4, polls=1))
    assert tick["poll_votes"] == 1
    assert tick["questions_asked"] == 2
    assert tick["reactions"] == 4
    assert tick["poll_participation"] == 100.0      # 1 vote / peak of 1 viewer


def test_multiple_votes_are_the_sum_across_polls(monkeypatch):
    # _counts already sums LivePoll.options[].votes across every poll; the tick relays it.
    tick = _tick(monkeypatch, _counts(poll_votes=3, polls=2), people=[
        VIEWER, {**VIEWER, "identity": "v2"}, {**VIEWER, "identity": "v3"}, {**VIEWER, "identity": "v4"}])
    assert tick["poll_votes"] == 3
    assert tick["poll_participation"] == 75.0       # 3 / peak 4


def test_no_activity_stays_zero(monkeypatch):
    tick = _tick(monkeypatch, _counts())
    assert (tick["poll_votes"], tick["reactions"], tick["questions_asked"]) == (0, 0, 0)
    assert tick["poll_participation"] == 0.0


def test_turnout_is_unavailable_not_zero_without_an_audience(monkeypatch):
    tick = _tick(monkeypatch, _counts(), people=[])
    assert tick["poll_participation"] is None


def test_later_ticks_follow_new_activity(monkeypatch):
    """The staleness itself: a vote cast after the console connected must surface."""
    h = _Harness(monkeypatch, [_session(uuid.uuid4())], people=[VIEWER])
    monkeypatch.setattr(bc, "_counts", lambda db, e, o: _counts())
    before = h.run()[0][1]
    monkeypatch.setattr(bc, "_counts", lambda db, e, o: _counts(poll_votes=1, polls=1))
    after = h.run()[0][1]
    assert before["poll_votes"] == 0 and after["poll_votes"] == 1


def test_tick_and_connect_snapshot_share_one_definition(monkeypatch):
    counts = _counts(poll_votes=2, questions=1, reactions=5)
    tick = _tick(monkeypatch, counts, peak=4)
    assert {k: tick[k] for k in COUNTER_FIELDS} == bc._interaction_totals(counts, 4)


def test_reactions_tile_is_message_reactions_not_the_floating_tally(monkeypatch):
    """The tile is captioned "On messages" and reads LiveMessage.reactions via _counts.
    The floating-reaction tally (bus.reaction_tally) is never sent to a client — this tick
    reaches every socket on the event — so the sampler must not read it."""
    async def forbidden(event_id):
        raise AssertionError("the analytics tick must not read the floating-reaction tally")
    monkeypatch.setattr(bc.bus, "reaction_all", forbidden)
    tick = _tick(monkeypatch, _counts(reactions=2))
    assert tick["reactions"] == 2


def test_engagement_is_unchanged_by_the_new_fields(monkeypatch):
    counts = _counts(messages=1, poll_votes=1)
    tick = _tick(monkeypatch, counts)
    assert tick["engagement"] == bc.engagement_score(counts, 1)


def test_an_unreadable_presence_still_omits_every_counter(monkeypatch):
    """Existing contract: an outage tick carries no counters, so the console keeps its last
    real numbers instead of being overwritten with invented zeros."""
    h = _Harness(monkeypatch, [_session(uuid.uuid4())], presence_raises=_connect_timeout())
    tick = h.run()[0][1]
    assert not COUNTER_FIELDS & set(tick)
