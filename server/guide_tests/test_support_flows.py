"""Deterministic viewer support (Conversation Flow §10–15; API §10; CF-AT-14..24,
API-AT-08/09, DB-AT-07, UX-AT-09/10)."""
import uuid

from sqlalchemy import func, select

from app.guide import event_context
from app.guide.models import GuideSession, GuideSupportStepEvent
from app.guide.support import flows as F

from conftest import create_session


def start(g, flow_type):
    res = g.post("/support-flows", {"flow_type": flow_type})
    assert res.status_code == 201, res.text
    return res.json()


def step(g, flow, option_id=None, command=None, client_step_id=None, step_id=None):
    body = {"client_step_id": str(client_step_id or uuid.uuid4()),
            "step_id": step_id or flow["step"]["step_id"]}
    if option_id:
        body["option_id"] = option_id
    if command:
        body["command"] = command
    return g.post(f"/support-flows/{flow['flow_id']}/steps", body)


def walk(g, flow, *options):
    for opt in options:
        res = step(g, flow, opt)
        assert res.status_code == 200, res.text
        flow = res.json()
    return flow


def session_state(db, g):
    db.expire_all()
    return db.get(GuideSession, uuid.UUID(g.session_id)).state


def test_definitions_are_closed_and_never_ask_for_free_text():
    F.validate_definitions()
    for flow in F.FLOWS.values():
        for s in flow.steps.values():
            assert s.input_type in ("choice", "boolean")  # no text input exists at all
            prompt = s.render_prompt(None).lower()
            assert "type your pin" not in prompt and "paste" not in prompt


def test_viewer_access_happy_path(guide, db):
    flow = start(guide, "viewer_access")
    assert flow["step"]["step_id"] == "invitation_source"
    assert flow["notice"] == F.SECRET_NOTICE
    assert session_state(db, guide) == "VIEWER_FLOW"
    flow = walk(guide, flow, "email_invitation", "yes", "no")
    # No D2 context -> timing step skipped -> generic resolve check.
    assert flow["step"]["step_id"] == "resolve_check"
    flow = walk(guide, flow, "yes")
    assert flow["status"] == "resolved" and flow["message"] == F.RESOLVED_MESSAGE
    assert session_state(db, guide) == "ACTIVE"


def test_pin_guidance_never_collects_the_pin(guide):
    flow = walk(guide, start(guide, "viewer_access"), "message_link", "yes", "yes")
    assert flow["step"]["step_id"] == "enter_on_page"
    assert "never here in Guide" in flow["step"]["prompt"]
    assert {o["option_id"] for o in flow["step"]["options"]} == {"worked", "not_accepted"}


def test_escalation_offers_registered_routes_only(guide, db):
    flow = walk(guide, start(guide, "viewer_access"), "other", "no", "no")
    assert flow["status"] == "escalated" and flow["resolution_code"] == "page_unreachable"
    assert flow["message"] == F.ESCALATE_GENERIC
    ids = [a["action_id"] for a in flow["actions"]]
    assert ids == ["viewer-support", "help-center"]
    assert all(a.get("href", "/").startswith("/") for a in flow["actions"])
    assert session_state(db, guide) == "SUPPORT_HANDOFF"


def test_escalation_with_public_organizer_route(client):
    token = event_context.issue({"organizer_contact_route": "org-help-1"})
    g = create_session(client, page_id="viewer", page_type="viewer", path="/watch",
                       public_event_context_token=token)
    flow = walk(g, start(g, "viewer_access"), "other", "no", "no")
    assert flow["message"] == F.ESCALATE_WITH_ORGANIZER
    assert flow["actions"][0]["action_id"] == "contact-organizer"


def test_timing_step_uses_only_validated_public_start(client):
    token = event_context.issue({"public_start_at": "2026-10-10T18:00:00Z",
                                 "public_timezone": "America/New_York",
                                 "public_status": "scheduled"})
    g = create_session(client, page_id="viewer", page_type="viewer", path="/watch",
                       public_event_context_token=token)
    flow = walk(g, start(g, "viewer_access"), "email_invitation", "yes", "no")
    assert flow["step"]["step_id"] == "event_timing"
    assert "Saturday, October 10 at 2:00 PM EDT" in flow["step"]["prompt"]
    assert "hasn't started yet" in flow["step"]["prompt"]


def test_stale_step_is_409_with_authoritative_state(guide):  # API-AT-09
    flow = start(guide, "viewer_access")
    step(guide, flow, "email_invitation")
    res = step(guide, flow, "other", step_id="invitation_source")
    assert res.status_code == 409 and res.json()["code"] == "GUIDE_STATE_CONFLICT"
    assert res.json()["current"]["step"]["step_id"] == "original_link"


def test_duplicate_client_step_replays_without_second_transition(guide, db):
    flow = start(guide, "playback")
    csid = uuid.uuid4()
    first = step(guide, flow, "buffering", client_step_id=csid)
    again = step(guide, flow, "buffering", client_step_id=csid)
    assert first.status_code == again.status_code == 200
    assert first.json()["step"] == again.json()["step"]
    assert db.scalar(select(func.count()).select_from(GuideSupportStepEvent)) == 1


def test_reusing_client_step_id_for_other_answer_conflicts(guide):
    flow = start(guide, "playback")
    csid = uuid.uuid4()
    step(guide, flow, "buffering", client_step_id=csid)
    res = step(guide, flow, "quality", client_step_id=csid, step_id="issue_type")
    assert res.status_code == 409


def test_unknown_option_is_422(guide):
    res = step(guide, start(guide, "playback"), "reboot_the_cdn")
    assert res.status_code == 422 and res.json()["code"] == "GUIDE_VALIDATION_ERROR"


def test_option_and_command_are_mutually_exclusive(guide):
    flow = start(guide, "playback")
    assert step(guide, flow, "buffering", command="back").status_code == 422
    assert step(guide, flow).status_code == 422


def test_back_and_restart(guide):
    flow = walk(guide, start(guide, "playback"), "buffering", "yes")
    assert flow["step"]["step_id"] == "refresh" and flow["step"]["allow_back"]
    back = step(guide, flow, command="back").json()
    assert back["step"]["step_id"] == "player_open"
    restarted = step(guide, back, command="restart").json()
    assert restarted["step"]["step_id"] == "issue_type"
    assert restarted["step"]["allow_back"] is False


def test_playback_never_claims_an_outage(guide):  # CF-AT-19
    flow = walk(guide, start(guide, "playback"), "buffering", "yes", "no", "no", "no")
    assert flow["step"]["step_id"] == "public_status"
    assert "doesn't by itself mean there's an outage" in flow["step"]["prompt"]
    assert flow["actions"][0]["action_id"] == "status-page"
    done = walk(guide, flow, "no")
    assert done["status"] == "escalated"


def test_switching_flows_from_audio_to_playback(guide, db):
    res = step(guide, start(guide, "audio"), "no")
    body = res.json()
    assert body["status"] == "cancelled" and body["resolution_code"] == "switched_to_playback"
    assert body["next_flow"]["flow_type"] == "playback"
    assert body["next_flow"]["step"]["step_id"] == "issue_type"
    assert session_state(db, guide) == "VIEWER_FLOW"


def test_category_flow_routes_to_specific_flow(guide):
    body = step(guide, start(guide, "viewer_other"), "captions").json()
    assert body["next_flow"]["flow_type"] == "captions"


def test_starting_a_new_flow_closes_the_previous_one(guide):
    first = start(guide, "playback")
    start(guide, "audio")
    res = step(guide, first, "buffering")
    assert res.status_code == 409


def test_captions_with_and_without_public_context(client, guide):  # CF-AT-21
    generic = start(guide, "captions")["step"]["prompt"]
    assert generic.startswith("I don't have public event information that confirms captions")
    token = event_context.issue({"caption_languages": ["en", "es-MX"]})
    g = create_session(client, page_id="viewer", page_type="viewer", path="/watch",
                       public_event_context_token=token)
    prompt = start(g, "captions")["step"]["prompt"]
    assert "captions for this event in English and Spanish" in prompt


def test_replay_without_context_makes_no_event_claim(guide):  # CF-AT-22
    prompt = start(guide, "replay")["step"]["prompt"]
    assert "I don't have public event information about a replay" in prompt
    assert "is available" not in prompt


def test_finished_flow_rejects_more_steps(guide):
    flow = walk(guide, start(guide, "replay"), "answered")
    res = step(guide, flow, "answered", step_id="replay_state")
    assert res.status_code == 409


def test_flow_of_another_session_is_not_found(client):
    a = create_session(client)
    flow = start(a, "playback")
    b = create_session(client)
    res = b.post(f"/support-flows/{flow['flow_id']}/steps",
                 {"client_step_id": str(uuid.uuid4()), "step_id": "issue_type",
                  "option_id": "buffering"})
    assert res.status_code == 404
