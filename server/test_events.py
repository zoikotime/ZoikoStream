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


def test_enforce_memorial_features_forces_all_four_off():
    fields = {"title": "x", "chat_enabled": True, "qa_enabled": True,
              "polls_enabled": True, "raise_hand_enabled": True}
    out, attempted = crud._enforce_memorial_features("Funeral / Memorial", dict(fields))
    assert out["chat_enabled"] is False
    assert out["qa_enabled"] is False
    assert out["polls_enabled"] is False
    assert out["raise_hand_enabled"] is False
    assert out["title"] == "x"  # untouched fields pass through
    # No visibility key in the input and no current_visibility passed — "attempted" is None,
    # which is itself the value being forced away from, matching visibility="private" below.
    assert attempted is None
    assert out["visibility"] == "private"


def test_enforce_memorial_features_injects_false_even_when_absent():
    """A partial update patch that never mentions chat_enabled must still come out False —
    this is what stops a category switch (Webinar -> Funeral / Memorial) from leaving a
    stale chat_enabled=True on the row."""
    out, _attempted = crud._enforce_memorial_features("Funeral / Memorial", {"category": "Funeral / Memorial"})
    assert out["chat_enabled"] is False
    assert out["raise_hand_enabled"] is False


def test_enforce_memorial_features_is_a_noop_for_other_categories():
    fields = {"chat_enabled": True, "qa_enabled": True}
    out, attempted = crud._enforce_memorial_features("Webinar", dict(fields))
    assert out == fields
    assert attempted is None


# ── P0.11 (audit 2026-08-27): memorial visibility is forced, not just displayed ───────────

def test_enforce_memorial_features_forces_visibility_private():
    out, attempted = crud._enforce_memorial_features("Funeral / Memorial", {"visibility": "public"})
    assert out["visibility"] == "private"
    assert attempted == "public"  # what was attempted, for the audit trail


def test_enforce_memorial_features_reports_unlisted_as_attempted_too():
    out, attempted = crud._enforce_memorial_features("Funeral / Memorial", {"visibility": "unlisted"})
    assert out["visibility"] == "private"
    assert attempted == "unlisted"


def test_enforce_memorial_features_no_correction_reported_when_already_private():
    out, attempted = crud._enforce_memorial_features("Funeral / Memorial", {"visibility": "private"})
    assert out["visibility"] == "private"
    assert attempted is None  # nothing to audit — the caller already sent the right value


def test_enforce_memorial_features_uses_current_visibility_when_field_untouched():
    """update_event's case: the caller's patch never mentions visibility at all. If the row is
    already private, nothing to correct; if it somehow isn't, that counts as an attempt too."""
    out, attempted = crud._enforce_memorial_features(
        "Funeral / Memorial", {"title": "x"}, current_visibility="private")
    assert out["visibility"] == "private"
    assert attempted is None

    out2, attempted2 = crud._enforce_memorial_features(
        "Funeral / Memorial", {"title": "x"}, current_visibility="public")
    assert out2["visibility"] == "private"
    assert attempted2 == "public"


def test_enforce_memorial_features_does_not_touch_visibility_for_other_categories():
    out, attempted = crud._enforce_memorial_features("Webinar", {"visibility": "public"})
    assert out["visibility"] == "public"
    assert attempted is None


def test_memorial_visibility_constant_is_a_valid_existing_enum_value():
    """Guards against MEMORIAL_VISIBILITY drifting out of sync with the actual Visibility
    enum — a typo here would silently write an invalid value to every memorial event."""
    from app.models import EVENT_VISIBILITY
    assert crud.MEMORIAL_VISIBILITY in EVENT_VISIBILITY


def test_create_event_audits_an_attempted_public_memorial():
    """create_event's audit call happens before the caller's own commit — a _StubSession-style
    fake proves the AuditLog is staged with the right facts without needing a real database."""
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
        "title": "Grandmother's memorial", "category": "Funeral / Memorial", "visibility": "public",
    })
    actor = SimpleNamespace(id=uuid.uuid4(), email="host@t.test")
    org_id = uuid.uuid4()
    ev = crud.create_event(_Stub(), org_id, actor.id, data, "grandmothers-memorial", actor=actor)

    assert ev.visibility == "private"  # never the attempted "public"
    entry = next(o for o in added if isinstance(o, AuditLog))
    assert entry.action == "event.memorial_visibility_enforced"
    assert entry.meta["attempted_visibility"] == "public"
    assert entry.meta["enforced_visibility"] == "private"
    assert entry.actor_id == actor.id


def test_create_event_does_not_audit_when_already_private():
    import uuid
    from types import SimpleNamespace
    from app.models import AuditLog

    added = []

    class _Stub:
        def add(self, o):
            added.append(o)

        def flush(self):
            pass

        def commit(self):
            pass

        def refresh(self, o):
            pass

    data = SimpleNamespace(model_dump=lambda exclude=None: {
        "title": "x", "category": "Funeral / Memorial", "visibility": "private",
    })
    actor = SimpleNamespace(id=uuid.uuid4(), email="host@t.test")
    crud.create_event(_Stub(), uuid.uuid4(), actor.id, data, "x", actor=actor)
    assert not any(isinstance(o, AuditLog) for o in added)


def test_create_event_non_memorial_is_never_audited_for_visibility():
    import uuid
    from types import SimpleNamespace
    from app.models import AuditLog

    added = []

    class _Stub:
        def add(self, o):
            added.append(o)

        def flush(self):
            pass

        def commit(self):
            pass

        def refresh(self, o):
            pass

    data = SimpleNamespace(model_dump=lambda exclude=None: {
        "title": "x", "category": "Webinar", "visibility": "public",
    })
    actor = SimpleNamespace(id=uuid.uuid4(), email="host@t.test")
    crud.create_event(_Stub(), uuid.uuid4(), actor.id, data, "x", actor=actor)
    assert not any(isinstance(o, AuditLog) for o in added)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("events self-check passed")
