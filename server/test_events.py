"""Self-check for Phase 4 event lifecycle validation + slug + duration. Pure logic,
no DB. Run: `python test_events.py` (or pytest)."""
from datetime import datetime, timedelta, timezone

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
