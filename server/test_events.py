"""Self-check for Phase 4 event lifecycle validation + slug + duration. Pure logic,
no DB. Run: `python test_events.py` (or pytest)."""
import types
from datetime import datetime, timedelta, timezone

from app.crud import commercial as commercial_crud
from app.crud import event as crud
from app.schemas.event import EventOut


FUTURE = datetime.now(timezone.utc) + timedelta(days=2)
PAST = datetime.now(timezone.utc) - timedelta(days=2)


def t(current, new, title="t", **kw):
    return crud.status_transition_error(current, new, title, **kw)


def test_publish_requires_title():
    assert t("draft", "published", None)
    assert t("draft", "published", "   ")   # whitespace-only
    assert t("draft", "scheduled", "", start_time=FUTURE)
    assert t("draft", "published", "My Event") is None


def test_scheduling_needs_a_real_future_start():
    assert "start date" in t("draft", "scheduled")
    assert "past" in t("draft", "scheduled", start_time=PAST)
    assert t("draft", "scheduled", start_time=FUTURE) is None
    assert t("published", "scheduled", start_time=FUTURE) is None
    assert crud.schedule_error(None) and crud.schedule_error(PAST)
    assert crud.schedule_error(datetime.now(timezone.utc) - timedelta(seconds=20)) is None   # clock skew


def test_only_the_broadcast_itself_enters_live_degraded_ended():
    """A status write alone can never claim a broadcast is running or over."""
    for target in ("live", "degraded", "ended"):
        assert t("published" if target == "live" else "live", target), target
    assert t("published", "live", actor="platform") is None
    assert t("scheduled", "live", actor="platform") is None
    assert t("armed", "live", actor="platform") is None
    assert t("live", "degraded", actor="platform") is None
    assert t("degraded", "live", actor="platform") is None
    assert t("live", "ended", actor="platform") is None
    assert t("degraded", "ended", actor="platform") is None


def test_live_only_from_published_scheduled_or_armed():
    for before in ("draft", "ready_to_arm", "ended", "cancelled", "archived"):
        assert t(before, "live", actor="platform"), before


def test_invalid_transitions_from_the_requirement_are_refused():
    assert t("ended", "live", actor="platform")
    assert t("cancelled", "live", actor="platform")
    assert t("archived", "armed", actor="archive")
    assert t("draft", "ending")                       # retired: not a status at all
    assert t("blocked", "live", actor="platform")     # retired: must be migrated first
    assert t("processing", "rehearsal")


def test_retired_statuses_are_not_statuses():
    for retired in ("rehearsal", "ending", "processing", "replay_ready", "blocked"):
        assert retired not in crud.EVENT_TRANSITIONS
        assert t("scheduled", retired) == f"'{retired}' is not an event status"


def test_noop_is_always_allowed():
    for status in crud.EVENT_TRANSITIONS:
        assert t(status, status) is None


def test_cancel_only_before_going_live():
    for before in ("draft", "published", "scheduled", "ready_to_arm", "armed"):
        assert t(before, "cancelled", title=None) is None, before
    assert "End the broadcast" in t("live", "cancelled")
    assert "End the broadcast" in t("degraded", "cancelled")
    assert t("ended", "cancelled")
    assert t("cancelled", "scheduled", start_time=FUTURE) == "A cancelled event can't be reopened"


def test_ready_to_arm_predecessors_and_way_back():
    assert t("published", "ready_to_arm") is None
    assert t("scheduled", "ready_to_arm") is None
    assert t("draft", "ready_to_arm")                 # not published yet
    assert t("ready_to_arm", "scheduled", start_time=FUTURE) is None   # mark not ready
    assert t("ready_to_arm", "published") is None


def test_armed_requires_readiness_and_can_be_disarmed():
    assert t("published", "armed", readiness_ready=True)          # wrong predecessor
    assert t("ready_to_arm", "armed")                             # readiness not confirmed
    assert t("ready_to_arm", "armed", readiness_ready=False)
    err = t("ready_to_arm", "armed", readiness_ready=False, readiness_reasons=["capacity not reserved"])
    assert "capacity not reserved" in err
    assert t("ready_to_arm", "armed", readiness_ready=True) is None
    assert t("armed", "ready_to_arm") is None                     # disarm
    assert t("ready_to_arm", "live", actor="platform")            # must arm first


def test_archive_and_unarchive_only_through_their_own_actions():
    for before in ("draft", "ended", "cancelled"):
        assert t(before, "archived", actor="archive") is None
        assert "Use Archive" in t(before, "archived")             # not via PATCH
        assert t("archived", before, actor="archive") is None
    assert "Use Unarchive" in t("archived", "ended")
    for active in ("published", "scheduled", "ready_to_arm", "armed", "live", "degraded"):
        assert t(active, "archived", actor="archive"), active
    assert t("archived", "live", actor="archive")
    assert t("live", "ended", actor="archive")                    # archive actor moves nothing else


def test_viewer_status_of_an_archived_event_is_what_it_was():
    ev = types.SimpleNamespace(status="archived", previous_status="cancelled")
    assert crud.viewer_status_of(ev) == "cancelled"
    ev.previous_status = None                                     # legacy archived row
    assert crud.viewer_status_of(ev) == "ended"
    assert crud.viewer_status_of(types.SimpleNamespace(status="live", previous_status=None)) == "live"


def test_transition_table_covers_exactly_the_canonical_statuses():
    from app.models.event import EVENT_STATUSES
    assert set(crud.EVENT_TRANSITIONS) == set(EVENT_STATUSES)
    for targets in crud.EVENT_TRANSITIONS.values():
        assert set(targets) <= set(EVENT_STATUSES)


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
                 allow_screen_share=True, auto_end_event=False,
                 status="scheduled", start_time=start, end_time=start + timedelta(minutes=90))
    assert e.duration_minutes == 90
    e2 = e.model_copy(update={"end_time": None})
    assert e2.duration_minutes is None


def test_is_memorial_category_matches_registry():
    assert crud.is_memorial_category("Funeral / Memorial") is True
    assert crud.is_memorial_category("Webinar") is False
    assert crud.is_memorial_category(None) is False
    assert crud.is_memorial_category("") is False


def test_is_memorial_category_ignores_case_and_whitespace():
    """category is free text — nothing server-side stops a direct API call from sending a
    casing/whitespace variant of the registry string, and that must not silently bypass
    the memorial restrictions (chat/Q&A/polls, dual-recording, watermark)."""
    assert crud.is_memorial_category("funeral / memorial") is True
    assert crud.is_memorial_category("FUNERAL / MEMORIAL") is True
    assert crud.is_memorial_category("  Funeral / Memorial  ") is True
    assert crud.is_memorial_category("Funeral/Memorial") is False  # not a synonym match, just case/whitespace


def test_category_min_risk_tier_ignores_case_and_whitespace():
    assert crud.category_min_risk_tier("funeral / memorial") == "r2"
    assert crud.category_min_risk_tier(" FUNERAL / MEMORIAL ") == "r2"
    assert crud.category_min_risk_tier("Webinar") == "r0"


# ── Category classifies; it no longer configures ───────────────────────────────────────
#
# The ten tests that stood here proved the opposite: that _enforce_memorial_features() forced
# chat/Q&A/polls/raise-hand off, rewrote visibility to "private", and staged an
# `event.memorial_visibility_enforced` AuditLog. That helper is retired along with the
# restriction, so the coverage is replaced rather than dropped — these assert that create_event
# writes back what the caller submitted, for the category that used to be the exception.

def test_create_event_keeps_a_public_memorial_public():
    """The exact case the old clamp existed to prevent, now the expected outcome."""
    import uuid
    from types import SimpleNamespace
    from app.models import AuditLog, Event

    added = []

    class _Stub:
        def add(self, o):
            added.append(o)
            if isinstance(o, Event) and o.id is None:
                o.id = uuid.uuid4()

        def flush(self):
            pass

        def commit(self):
            pass

        def refresh(self, o):
            pass

    data = SimpleNamespace(model_dump=lambda exclude=None: {
        "title": "Grandmother's memorial", "category": "Funeral / Memorial",
        "visibility": "public", "chat_enabled": True, "qa_enabled": True,
        "polls_enabled": True, "raise_hand_enabled": True,
    })
    actor = SimpleNamespace(id=uuid.uuid4(), email="host@t.test")
    ev = crud.create_event(_Stub(), uuid.uuid4(), actor.id, data, "grandmothers-memorial",
                           actor=actor)

    assert ev.visibility == "public"
    assert (ev.chat_enabled, ev.qa_enabled) == (True, True)
    assert (ev.polls_enabled, ev.raise_hand_enabled) == (True, True)
    # No override happened, so nothing may be audited as one.
    assert not [o for o in added if isinstance(o, AuditLog)]


def test_the_forcing_helpers_are_gone():
    """Named explicitly so a re-introduction is a deliberate act, not an accident."""
    for gone in ("_enforce_memorial_features", "_audit_memorial_visibility_correction",
                 "MEMORIAL_VISIBILITY", "_MEMORIAL_DISABLED_FEATURES"):
        assert not hasattr(crud, gone), f"{gone} is back"


def test_the_commercial_risk_floor_survives():
    """Kept deliberately: risk_tier drives service profiles, cancellation policies, order
    pricing and readiness gates in crud/commercial.py. It never gated visibility or features,
    so retiring the audience restriction must not lower it."""
    assert crud.category_min_risk_tier("Funeral / Memorial") == "r2"
    assert crud.elevated_risk_tier("Funeral / Memorial", "r0") == "r2"
    # Still a floor, never a ceiling.
    assert crud.elevated_risk_tier("Funeral / Memorial", "r3") == "r3"
    assert crud.elevated_risk_tier("Webinar", "r0") == "r0"


def test_valid_and_invalid_timezones():
    assert crud.is_valid_timezone("UTC") is True
    assert crud.is_valid_timezone("Asia/Kolkata") is True
    assert crud.is_valid_timezone("America/New_York") is True
    assert crud.is_valid_timezone("Europe/London") is True
    assert crud.is_valid_timezone(None) is True
    assert crud.is_valid_timezone("") is True
    assert crud.is_valid_timezone("Invalid/Fake_Zone") is False
    assert crud.is_valid_timezone("NotATimezone") is False


def test_schedule_validation_enforces_title_and_future_start():
    # Missing title
    assert "without a title" in crud.status_transition_error("draft", "scheduled", "", start_time=FUTURE)
    assert "without a title" in crud.status_transition_error("draft", "scheduled", "   ", start_time=FUTURE)
    # Missing start time
    assert "start date" in crud.status_transition_error("draft", "scheduled", "Valid Title", start_time=None)
    # Past start time
    assert "past" in crud.status_transition_error("draft", "scheduled", "Valid Title", start_time=PAST)
    # Valid schedule
    assert crud.status_transition_error("draft", "scheduled", "Valid Title", start_time=FUTURE) is None


def test_draft_allows_empty_start_and_empty_title():
    # Draft does not require title or future start
    assert crud.status_transition_error("draft", "draft", "", start_time=None) is None
    assert crud.status_transition_error("draft", "draft", "Draft Event", start_time=PAST) is None


def test_create_event_preserves_timezone_and_schedule_fields():
    import uuid
    from types import SimpleNamespace
    from app.models import Event

    class _StubDb:
        def __init__(self):
            self.added = []
        def add(self, o):
            self.added.append(o)
            if isinstance(o, Event) and o.id is None:
                o.id = uuid.uuid4()
        def flush(self): pass
        def commit(self): pass
        def refresh(self, o): pass

    start = datetime(2026, 10, 6, 10, 26, tzinfo=timezone.utc)
    end = datetime(2026, 10, 6, 10, 36, tzinfo=timezone.utc)
    data = SimpleNamespace(model_dump=lambda exclude=None: {
        "title": "Scheduled Kolkata Event",
        "category": "Sports",
        "timezone": "Asia/Kolkata",
        "start_time": start,
        "end_time": end,
        "status": "scheduled",
        "visibility": "public",
        "chat_enabled": False,
        "polls_enabled": False,
        "qa_enabled": False,
        "recording_enabled": True,
    })
    actor = SimpleNamespace(id=uuid.uuid4(), email="admin@zoikostream.com")
    db = _StubDb()
    ev = crud.create_event(db, uuid.uuid4(), actor.id, data, "scheduled-kolkata-event", actor=actor)

    assert ev.title == "Scheduled Kolkata Event"
    assert ev.status == "scheduled"
    assert ev.timezone == "Asia/Kolkata"
    assert ev.start_time == start
    assert ev.end_time == end


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("events self-check passed")
