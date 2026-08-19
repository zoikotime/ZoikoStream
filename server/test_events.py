"""Self-check for Phase 4 event lifecycle validation + slug + duration. Pure logic,
no DB. Run: `python test_events.py` (or pytest)."""
import types
from datetime import datetime, timedelta, timezone

from app.crud import commercial as commercial_crud
from app.crud import event as crud
from app.schemas.event import EventOut


def test_publish_requires_title():
    assert crud.status_transition_error("draft", "published", None)
    assert crud.status_transition_error("draft", "published", "   ")   # whitespace-only
    assert crud.status_transition_error("draft", "scheduled", "")
    assert crud.status_transition_error("draft", "published", "My Event") is None


def test_live_only_from_published():
    assert crud.status_transition_error("published", "live", "t") is None
    assert crud.status_transition_error("scheduled", "live", "t") is None
    assert crud.status_transition_error("draft", "live", "t")            # not published
    assert crud.status_transition_error("ended", "live", "t")


def test_end_only_from_live():
    assert crud.status_transition_error("live", "ended", "t") is None
    assert crud.status_transition_error("published", "ended", "t")
    assert crud.status_transition_error("draft", "ended", "t")


def test_cannot_archive_live():
    assert crud.status_transition_error("live", "archived", "t")
    assert crud.status_transition_error("ended", "archived", "t") is None
    assert crud.status_transition_error("cancelled", "archived", "t") is None


def test_noop_and_cancel_allowed():
    assert crud.status_transition_error("live", "live", "t") is None       # no-op
    assert crud.status_transition_error("live", "cancelled", "t") is None   # cancel unrestricted
    assert crud.status_transition_error("draft", "cancelled", None) is None


def test_ready_to_arm_predecessors():
    assert crud.status_transition_error("published", "ready_to_arm", "t") is None
    assert crud.status_transition_error("scheduled", "ready_to_arm", "t") is None
    assert crud.status_transition_error("rehearsal", "ready_to_arm", "t") is None
    assert crud.status_transition_error("draft", "ready_to_arm", "t")        # not published yet
    assert crud.status_transition_error("live", "ready_to_arm", "t")


def test_armed_requires_readiness():
    # Wrong predecessor, even with readiness satisfied.
    assert crud.status_transition_error("published", "armed", "t", readiness_ready=True)
    # Right predecessor, but readiness not (yet) confirmed True.
    assert crud.status_transition_error("ready_to_arm", "armed", "t")
    assert crud.status_transition_error("ready_to_arm", "armed", "t", readiness_ready=False)
    err = crud.status_transition_error(
        "ready_to_arm", "armed", "t", readiness_ready=False, readiness_reasons=["capacity not reserved"],
    )
    assert "capacity not reserved" in err
    # Right predecessor + confirmed readiness -> allowed.
    assert crud.status_transition_error("ready_to_arm", "armed", "t", readiness_ready=True) is None


def test_live_from_armed():
    assert crud.status_transition_error("armed", "live", "t") is None
    assert crud.status_transition_error("published", "live", "t") is None   # unchanged path
    assert crud.status_transition_error("ready_to_arm", "live", "t")        # must arm first


def test_degraded_only_from_live():
    assert crud.status_transition_error("live", "degraded", "t") is None
    assert crud.status_transition_error("armed", "degraded", "t")
    assert crud.status_transition_error("published", "degraded", "t")


def test_ending_processing_replay_chain():
    assert crud.status_transition_error("live", "ending", "t") is None
    assert crud.status_transition_error("degraded", "ending", "t") is None
    assert crud.status_transition_error("armed", "ending", "t")
    assert crud.status_transition_error("ending", "processing", "t") is None
    assert crud.status_transition_error("live", "processing", "t")
    assert crud.status_transition_error("processing", "replay_ready", "t") is None
    assert crud.status_transition_error("ending", "replay_ready", "t")


def test_ended_allows_degraded_and_replay_ready():
    assert crud.status_transition_error("live", "ended", "t") is None       # unchanged path
    assert crud.status_transition_error("degraded", "ended", "t") is None
    assert crud.status_transition_error("replay_ready", "ended", "t") is None
    assert crud.status_transition_error("published", "ended", "t")
    assert crud.status_transition_error("draft", "ended", "t")


def test_archived_blocks_active_states():
    for active in ("live", "armed", "degraded", "ending", "processing"):
        assert crud.status_transition_error(active, "archived", "t")
    assert crud.status_transition_error("ended", "archived", "t") is None
    assert crud.status_transition_error("cancelled", "archived", "t") is None


def test_category_risk_tier_floor():
    assert crud.elevated_risk_tier("Funeral / Memorial", "r0") == "r2"
    assert crud.elevated_risk_tier("Funeral / Memorial", "r3") == "r3"      # never lowers
    assert crud.elevated_risk_tier("Webinar", "r0") == "r0"                 # no floor defined
    assert crud.elevated_risk_tier(None, "r1") == "r1"
    assert crud.elevated_risk_tier("Funeral / Memorial", "r1") == "r2"      # raises to the floor


def _fake_session(**overrides):
    """Same technique as test_contributor.py's _session — contributor_readiness_reasons
    only reads a few attributes, so a SimpleNamespace stands in for a ContributorSession
    row without a DB."""
    base = dict(state="waiting", consent_given=False, preflight_result=None, rehearsal_complete=False)
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_contributor_readiness_not_invited():
    reasons = commercial_crud.contributor_readiness_reasons([("Sam Speaker", None)])
    assert reasons == ["contributor 'Sam Speaker' has not been invited"]


def test_contributor_readiness_removed_counts_as_not_invited():
    reasons = commercial_crud.contributor_readiness_reasons([("Sam Speaker", _fake_session(state="removed"))])
    assert reasons == ["contributor 'Sam Speaker' has not been invited"]


def test_contributor_readiness_reports_every_missing_step():
    reasons = commercial_crud.contributor_readiness_reasons([("Sam Speaker", _fake_session())])
    assert reasons == [
        "contributor 'Sam Speaker' has not given consent",
        "contributor 'Sam Speaker' has not completed preflight",
        "contributor 'Sam Speaker' has not completed rehearsal",
    ]


def test_contributor_readiness_passes_once_everything_is_done():
    ready = _fake_session(state="ready", consent_given=True,
                          preflight_result={"passed": True}, rehearsal_complete=True)
    assert commercial_crud.contributor_readiness_reasons([("Sam Speaker", ready)]) == []


def test_contributor_readiness_no_contributors_is_a_no_op():
    assert commercial_crud.contributor_readiness_reasons([]) == []


def test_slugify():
    assert crud.slugify("Tech Summit 2024!") == "tech-summit-2024"
    assert crud.slugify("  --Hello--  ") == "hello"
    assert crud.slugify("") == "event"


def test_unique_slug_appends_suffix():
    taken = {"tech-summit", "tech-summit-1"}
    orig = crud.event_slug_taken
    crud.event_slug_taken = lambda db, org, slug, exclude_id=None: slug.lower() in taken
    try:
        assert crud.unique_event_slug(None, "o1", "Tech Summit") == "tech-summit-2"
        assert crud.unique_event_slug(None, "o1", "Fresh Name") == "fresh-name"
    finally:
        crud.event_slug_taken = orig


def test_duration_computed():
    start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    e = EventOut(id="11111111-1111-1111-1111-111111111111",
                 org_id="22222222-2222-2222-2222-222222222222",
                 created_by="33333333-3333-3333-3333-333333333333",
                 visibility="public", registration_required=False,
                 waiting_room_enabled=False, recording_enabled=False, chat_enabled=True,
                 qa_enabled=True, polls_enabled=False, raise_hand_enabled=True,
                 allow_screen_share=True, auto_start_recording=False, auto_end_event=False,
                 status="scheduled", start_time=start, end_time=start + timedelta(minutes=90))
    assert e.duration_minutes == 90
    e2 = e.model_copy(update={"end_time": None})
    assert e2.duration_minutes is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("events self-check passed")
