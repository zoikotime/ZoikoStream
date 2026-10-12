"""Questions about the event being watched (CF §16, §21.1; PRD PR-020/PR-023; PG-AT-15)."""
import uuid

import pytest

from app.guide import event_answers, event_context, policy
from conftest import create_session

CLAIMS = {"event_display_name": "Town Hall", "public_start_at": "2026-10-12T06:05:00Z",
          "public_timezone": "Asia/Kolkata", "public_status": "live",
          "replay_public_state": "unavailable"}
GAP = ("I don't have public event information that confirms that. The event invitation or "
       "the organizer has those details.")


@pytest.mark.parametrize("question,expected", [
    ("What time does it start?", "Town Hall's published start time is Monday, October 12 at 11:35 AM IST."),
    ("is it live yet?", "Town Hall is live now."),
    ("will there be a replay?", "The public event context does not show replay availability for this event."),
    ("tell me about this event", "Town Hall is live now. Town Hall's published start time is"),
])
def test_public_facts_are_answered_from_the_event_context(question, expected):
    reply = event_answers.answer(question, CLAIMS)
    assert reply.grounded and reply.text.startswith(expected)


@pytest.mark.parametrize("question", [
    "duriation of this event", "how long is it?", "when does it end?", "how much time is left?",
    "who is hosting this event?", "is the host john speaking right now?",
    "what's the agenda?", "give me the event description",
])
def test_details_outside_the_public_claims_get_the_exact_gap_reply(question):
    reply = event_answers.answer(question, CLAIMS)
    assert reply is not None and not reply.grounded and reply.text == GAP


def test_gap_reply_without_context_only_when_the_question_names_this_event():
    assert event_answers.answer("how long is this event?", None).text == GAP
    assert event_answers.answer("how long is the onboarding?", None) is None


@pytest.mark.parametrize("question", [
    "the captions aren't working", "video keeps buffering",
    "how long can a ZoikoStream event be", "what are your pricing plans",
])
def test_problems_and_product_questions_are_left_to_normal_routing(question):
    assert event_answers.answer(question, CLAIMS) is None


def test_unknown_replay_and_caption_facts_go_to_the_support_flows():
    claims = {**CLAIMS, "replay_public_state": "unknown"}
    assert event_answers.answer("will there be a replay?", claims) is None
    assert event_answers.answer("how do I turn on captions?", claims) is None


def test_published_caption_languages_are_stated_exactly():
    reply = event_answers.answer("are there captions?", {**CLAIMS, "caption_languages": ["en", "es"]})
    assert reply.text == "This event lists captions in: en, es."


def test_claims_outside_the_spec_allowlist_are_dropped():
    v = event_context.verify(event_context.issue({
        **CLAIMS, "public_end_at": "2026-10-12T08:05:00Z", "duration_minutes": 120,
        "host_display_name": "John", "event_summary": "x", "features": {"chat": True}}))
    assert set(v.safe_claims) == set(CLAIMS)


@pytest.mark.parametrize("text", ["change the replay settings", "edit the recording options",
                                  "disable the replay for this event", "start the stream"])
def test_event_control_requests_are_boundaries(text):
    assert policy.classify_boundary(text).reason == "operator"


def test_viewer_chat_end_to_end(client):
    g = create_session(client, page_id="viewer", page_type="viewer", path="/watch",
                       public_event_context_token=event_context.issue(CLAIMS))
    assert g.body["event_context"]["public_start_at"] == "2026-10-12T06:05:00Z"

    def ask(text):
        res = g.post("/messages", {"client_message_id": str(uuid.uuid4()), "text": text})
        assert res.status_code == 200, res.text
        return res.json()["assistant_message"]

    fact = ask("what time does it start?")
    assert fact["grounded"] and fact["sources"][0]["source_id"] == "event-context"
    gap = ask("duration of this event")
    assert gap["text"].startswith(GAP) and gap["answer_class"] == "knowledge_gap"
    assert gap["sources"] == []
    boundary = ask("change the replay settings")
    assert boundary["answer_class"] == "restricted"
