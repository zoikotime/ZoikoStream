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


# ── Flow switching needs confirmation once there is progress (C3; CF §11) ─────────────────

def test_switching_away_from_a_flow_with_progress_requires_confirmation(guide, db):
    first = walk(guide, start(guide, "playback"), "buffering")
    res = guide.post("/support-flows", {"flow_type": "audio"})
    assert res.status_code == 409
    body = res.json()
    assert body["code"] == "GUIDE_STATE_CONFLICT" and body["confirm_required"] is True
    assert body["active_flow_type"] == "playback"
    assert body["current"]["step"]["step_id"] == first["step"]["step_id"]
    # Nothing was lost: the playback flow still accepts its next step.
    assert step(guide, first, "yes").status_code == 200

    confirmed = guide.post("/support-flows", {"flow_type": "audio", "confirm_replace": True})
    assert confirmed.status_code == 201 and confirmed.json()["flow_type"] == "audio"
    assert step(guide, first, "yes", step_id="refresh").status_code == 409  # replaced


def test_switching_from_an_untouched_flow_needs_no_confirmation(guide):
    start(guide, "playback")
    assert guide.post("/support-flows", {"flow_type": "audio"}).status_code == 201


# ── Idempotent start (API §16) ────────────────────────────────────────────────────────────

def test_start_with_same_client_request_id_returns_same_flow(guide, db):
    rid = str(uuid.uuid4())
    a = guide.post("/support-flows", {"flow_type": "playback", "client_request_id": rid})
    walk(guide, a.json(), "buffering")
    b = guide.post("/support-flows", {"flow_type": "playback", "client_request_id": rid})
    assert a.status_code == b.status_code == 201
    assert b.json()["flow_id"] == a.json()["flow_id"] and b.json()["replayed"] is True
    assert b.json()["step"]["step_id"] == "player_open"  # current state, not restarted
    from app.guide.models import GuideSupportFlow
    assert db.scalar(select(func.count()).select_from(GuideSupportFlow)) == 1


def test_client_request_id_reused_for_other_flow_type_conflicts(guide):
    rid = str(uuid.uuid4())
    guide.post("/support-flows", {"flow_type": "playback", "client_request_id": rid})
    res = guide.post("/support-flows", {"flow_type": "audio", "client_request_id": rid})
    assert res.status_code == 409


def test_client_request_id_is_scoped_to_the_session(client):
    rid = str(uuid.uuid4())
    a = create_session(client)  # one cookie jar: use each session before creating the next
    fa = a.post("/support-flows", {"flow_type": "replay", "client_request_id": rid}).json()
    b = create_session(client)
    fb = b.post("/support-flows", {"flow_type": "replay", "client_request_id": rid}).json()
    assert fa["flow_id"] != fb["flow_id"]


# ── Audio end to end (CF §13) ─────────────────────────────────────────────────────────────

def test_audio_flow_resolves_at_device_volume(guide, db):
    flow = start(guide, "audio")
    assert flow["step"]["step_id"] == "video_playing"
    flow = walk(guide, flow, "yes", "no")
    assert flow["step"]["step_id"] == "device_volume"
    done = walk(guide, flow, "yes")
    assert done["status"] == "resolved" and done["message"] == F.RESOLVED_MESSAGE
    assert session_state(db, guide) == "ACTIVE"


def test_audio_flow_escalates_after_all_steps(guide, db):
    done = walk(guide, start(guide, "audio"), "yes", "no", "no", "no")
    assert done["status"] == "escalated" and done["resolution_code"] == "audio_not_resolved"
    assert {a["action_id"] for a in done["actions"]} >= {"viewer-support"}
    assert session_state(db, guide) == "SUPPORT_HANDOFF"


# ── Playback: early resolution and the PB-06 status card (CF §12; C5) ─────────────────────

def test_playback_resolves_early_after_refresh(guide, db):
    done = walk(guide, start(guide, "playback"), "not_loading", "yes", "yes")
    assert done["status"] == "resolved" and done["step"] is None
    assert session_state(db, guide) == "ACTIVE"


def test_status_card_unknown_when_d1_unverifiable(guide):
    flow = walk(guide, start(guide, "playback"), "buffering", "yes", "no", "no")
    assert "status_card" not in flow  # only at PB-06
    flow = walk(guide, flow, "no")
    card = flow["status_card"]
    assert card["state"] == "unknown" and card["status_url"] == "/status"
    assert "can't verify" in card["summary"]


def test_status_card_reports_d1_outage_only_when_d1_says_so(guide, monkeypatch):
    from datetime import datetime, timezone
    from app.guide import status as public_status
    checked = datetime(2026, 10, 23, 14, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(public_status, "current", lambda: public_status.PublicStatus(
        "Major outage", [("Playback errors", "investigating")], 0, checked))
    flow = walk(guide, start(guide, "playback"), "buffering", "yes", "no", "no", "no")
    card = flow["status_card"]
    assert card["state"] == "outage" and card["checked_at"] == "2026-10-23T14:05:00Z"
    assert "Playback errors" in card["summary"]
    monkeypatch.setattr(public_status, "current", lambda: public_status.PublicStatus(
        "No customer impact", [], 0, checked))
    replay = step(guide, flow, command="back").json()
    again = walk(guide, replay, "no")
    assert again["status_card"]["state"] == "operational"


# ── Replay with D2 (CF §15) ───────────────────────────────────────────────────────────────

def test_replay_available_and_pending_with_public_context(client):
    for state, phrase in (("available", "a replay is available"),
                          ("pending", "being prepared but isn't available yet"),
                          ("unavailable", "doesn't show a replay")):
        token = event_context.issue({"replay_public_state": state})
        g = create_session(client, page_id="viewer", page_type="viewer", path="/watch",
                           public_event_context_token=token)
        prompt = start(g, "replay")["step"]["prompt"]
        assert phrase in prompt, (state, prompt)
