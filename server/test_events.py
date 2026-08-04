"""Self-check for the event lifecycle validation, slug, duration, and the event-management
module's pure logic (transitions, bulk verb mapping, duplicate safety, access-link tokens,
viewer access rules). Pure logic, no DB. Run: `python test_events.py` (or pytest)."""
import typing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.crud import event as crud
from app.models import ASSIGNMENT_ROLES, EVENT_STATUSES, EVENT_VISIBILITY
from app.routers.events import BULK_TARGET
from app.schemas.event import BulkActionName, EventOut, TeamOut
from app.services import viewer as viewer_svc


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
                 captions_enabled=False, translation_enabled=False,
                 waiting_room_enabled=False, recording_enabled=False, chat_enabled=True,
                 qa_enabled=True, polls_enabled=False, raise_hand_enabled=True,
                 allow_screen_share=True, auto_start_recording=False, auto_end_event=False,
                 status="scheduled", start_time=start, end_time=start + timedelta(minutes=90))
    assert e.duration_minutes == 90
    e2 = e.model_copy(update={"end_time": None})
    assert e2.duration_minutes is None


# ── Event-management module ───────────────────────────────────────────────────

def test_pause_and_resume_transitions():
    # A pause is only legal from live, and it is a springboard back to live (a resume), which
    # is what stops services.broadcast._resume from having to bypass the guard.
    assert crud.status_transition_error("live", "paused", "t") is None
    assert crud.status_transition_error("paused", "live", "t") is None
    assert crud.status_transition_error("published", "paused", "t")
    assert crud.status_transition_error("ended", "paused", "t")
    # Ending from a pause must work, or a host who paused then ended leaves the event live.
    assert crud.status_transition_error("paused", "ended", "t") is None
    # An event still on air cannot be archived, in either on-air state.
    assert crud.status_transition_error("paused", "archived", "t")
    assert crud.status_transition_error("live", "archived", "t")


def test_unpublish_only_from_published_or_scheduled():
    assert crud.status_transition_error("published", "draft", "t") is None
    assert crud.status_transition_error("scheduled", "draft", "t") is None
    # Anything else back to draft would resurrect a finished event.
    for state in ("live", "paused", "ended", "cancelled", "archived"):
        assert crud.status_transition_error(state, "draft", "t"), state


def test_every_status_and_visibility_is_reachable_vocabulary():
    # Guards against the model and the schema drifting apart — the Literal in
    # schemas/event.py and these tuples have to describe the same state machine.
    from app.schemas.event import EventStatus, Visibility

    assert set(typing.get_args(EventStatus)) == set(EVENT_STATUSES)
    assert set(typing.get_args(Visibility)) == set(EVENT_VISIBILITY)


def test_bulk_verbs_all_map_to_a_target():
    """Every verb the schema accepts must have a status mapping (or "delete"), otherwise the
    endpoint KeyErrors on a request the framework already validated."""
    verbs = set(typing.get_args(BulkActionName))
    assert verbs - {"delete"} == set(BULK_TARGET), verbs.symmetric_difference(BULK_TARGET)
    for verb, target in BULK_TARGET.items():
        assert target in EVENT_STATUSES, verb


def test_team_payload_covers_every_role():
    """TeamOut must have a field per assignment role, or a newly added role would be silently
    dropped from the detail page's single team request."""
    assert set(TeamOut.model_fields) == set(ASSIGNMENT_ROLES)


def test_duplicate_never_copies_secrets_or_identity():
    """The copy list is the security boundary of the duplicate feature: a passphrase, the
    slug, the status and the schedule must never be inherited."""
    forbidden = {
        "access_password_hash", "id", "slug", "org_id", "created_by", "status",
        "start_time", "end_time", "created_at", "updated_at", "deleted_at",
    }
    assert not (set(crud._DUPLICABLE) & forbidden), set(crud._DUPLICABLE) & forbidden
    # And it must still carry the configuration, or "duplicate" is just "create".
    for field in ("visibility", "chat_enabled", "recording_enabled", "replay_enabled",
                  "stream_quality", "registration_required"):
        assert field in crud._DUPLICABLE, field


def test_access_link_token_is_hashed_not_stored():
    raw, token_hash = crud._new_token()
    assert len(raw) >= 32                             # secrets.token_urlsafe(32)
    assert token_hash != raw                          # raw never stored
    assert len(token_hash) == 64                      # sha256 hex
    assert crud._hash_token(raw) == token_hash        # deterministic lookup key
    assert crud._hash_token("abc") != crud._hash_token("abd")
    # Two links never collide on the same secret.
    assert crud._new_token()[0] != crud._new_token()[0]


# ── Viewer access rules (pure: access_for only reads attributes) ──────────────

def _ev(**kw):
    base = dict(org_id="org-1", visibility="public", access_password_hash=None, status="live",
                registration_required=False)
    return SimpleNamespace(**{**base, **kw})


def _user(role="viewer", org_id="org-2"):
    return SimpleNamespace(role=role, org_id=org_id)


def test_access_public_and_unlisted_open_to_any_session():
    for vis in ("public", "unlisted"):
        allowed, basis, _ = viewer_svc.access_for(_ev(visibility=vis), _user())
        assert allowed and basis == f"{vis}_event"


def test_access_private_is_org_only():
    allowed, _, reason = viewer_svc.access_for(_ev(visibility="private"), _user())
    assert not allowed and reason
    allowed, basis, _ = viewer_svc.access_for(_ev(visibility="private"), _user(org_id="org-1"))
    assert allowed and basis == "organization_member"


def test_access_invite_only_needs_a_link():
    ev = _ev(visibility="invite_only")
    allowed, _, reason = viewer_svc.access_for(ev, _user())
    assert not allowed and "invitation" in reason
    allowed, basis, _ = viewer_svc.access_for(ev, _user(), link_ok=True)
    assert allowed and basis == "access_link"
    # An org member never needs a link to their own event.
    assert viewer_svc.access_for(ev, _user(org_id="org-1"))[0]


def test_access_link_does_not_open_a_private_event_to_outsiders_by_accident():
    # A link IS the mechanism for private events too — but only because the org admin issued
    # it for that event; the token lookup is event-scoped in crud.find_access_link.
    assert viewer_svc.access_for(_ev(visibility="private"), _user(), link_ok=True)[0]
    # Without one, private stays closed.
    assert not viewer_svc.access_for(_ev(visibility="private"), _user())[0]


def test_super_admin_always_allowed():
    allowed, basis, _ = viewer_svc.access_for(_ev(visibility="private"), _user(role="super_admin"))
    assert allowed and basis == "platform_admin"


def test_password_gate():
    from app.security import hash_password

    ev = _ev(access_password_hash=hash_password("letmein"))
    assert viewer_svc.password_gate(_ev(), None) is None            # no passphrase set
    assert viewer_svc.password_gate(ev, None)                       # missing
    assert viewer_svc.password_gate(ev, "wrong")                    # incorrect
    assert viewer_svc.password_gate(ev, "letmein") is None          # correct
    # The organizing org and platform admins are never locked out by their own gate.
    assert viewer_svc.password_gate(ev, None, exempt=True) is None


def test_playback_allowed_while_paused_but_not_before_or_after():
    # Patch the LiveKit check: this asserts the STATUS rule, not the deployment's config.
    original = viewer_svc.livekit.configured
    viewer_svc.livekit.configured = lambda: True
    try:
        assert viewer_svc.playback_blocked_reason(_ev(status="live")) is None
        assert viewer_svc.playback_blocked_reason(_ev(status="paused")) is None
        for state in ("published", "scheduled", "ended", "cancelled"):
            assert viewer_svc.playback_blocked_reason(_ev(status=state)), state
    finally:
        viewer_svc.livekit.configured = original


def test_viewable_statuses_exclude_draft_and_archived():
    """A draft or archived event must not exist as far as an attendee is concerned."""
    assert "draft" not in viewer_svc.VIEWABLE_STATUSES
    assert "archived" not in viewer_svc.VIEWABLE_STATUSES
    assert "paused" in viewer_svc.VIEWABLE_STATUSES   # a pause is not an ending


def test_event_out_reports_password_without_leaking_the_hash():
    e = EventOut(id="11111111-1111-1111-1111-111111111111",
                 org_id="22222222-2222-2222-2222-222222222222",
                 created_by="33333333-3333-3333-3333-333333333333",
                 visibility="public", registration_required=False,
                 captions_enabled=False, translation_enabled=False,
                 waiting_room_enabled=False, recording_enabled=False, chat_enabled=True,
                 qa_enabled=True, polls_enabled=False, raise_hand_enabled=True,
                 allow_screen_share=True, auto_start_recording=False, auto_end_event=False,
                 status="draft", access_password_hash="$2b$12$fakehashfakehashfakehash")
    dumped = e.model_dump()
    assert dumped["password_protected"] is True
    assert "access_password_hash" not in dumped, "the passphrase hash must never be serialized"
    # Registration is deliberately unreported until there is a registrations table.
    assert dumped["registrations"] is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("events self-check passed")
