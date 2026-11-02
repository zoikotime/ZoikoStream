"""Questions about the event being watched (CF §16, §21.1; PRD PR-020/PR-023; PG-AT-15)."""
import uuid

import pytest

from app.guide import event_answers, event_context, policy
from conftest import create_session

CLAIMS = {"event_display_name": "Town Hall", "public_start_at": "2026-10-12T06:05:00Z",
          "public_timezone": "Asia/Kolkata", "public_status": "live",
          "replay_public_state": "unavailable"}
FULL = {**CLAIMS, "public_end_at": "2026-10-12T07:35:00Z", "host_display_name": "John",
        "event_summary": "Quarterly company update", "event_category": "Webinar",
        "chat_enabled": True, "qa_enabled": False}
GAP = ("I don't have public event information that confirms that. The event invitation or "
       "the organizer has those details.")


@pytest.mark.parametrize("question,expected", [
    ("What time does it start?", "Town Hall's published start time is Monday, October 12 at 11:35 AM IST."),
    ("is it live yet?", "Town Hall is live now."),
    ("will there be a replay?", "The public event context does not show replay availability for this event."),
    ("tell me about this event", "Town Hall is live now. Town Hall's published start time is"),
    ("at what time meeting started", "Town Hall's published start time is"),
    ("what is the meeting about", "Town Hall is live now. Town Hall's published start time is"),
    ("is the meeting live?", "Town Hall is live now."),
])
def test_public_facts_are_answered_from_the_event_context(question, expected):
    reply = event_answers.answer(question, CLAIMS)
    assert reply.grounded and reply.text.startswith(expected)


@pytest.mark.parametrize("question,expected", [
    ("what is end time?", "Town Hall is scheduled to end at Monday, October 12 at 1:05 PM IST."),
    ("at which time meeting will end", "Town Hall is scheduled to end at"),
    ("duration of event", "Town Hall is scheduled to run for 1 hour 30 minutes."),
    ("how long is it?", "Town Hall is scheduled to run for 1 hour 30 minutes."),
    ("who is hosting this event?", "Town Hall is hosted by John."),
    ("give me the event description", "About Town Hall: Quarterly company update."),
    ("what's the agenda?", "About Town Hall: Quarterly company update."),
    ("what is the meeting about", "About Town Hall: Quarterly company update. Town Hall is live now."),
    ("give time in UTC", "Town Hall starts at Monday, October 12 at 6:05 AM UTC and ends at Monday, October 12 at 7:35 AM UTC."),
    ("what is the end time in GMT?", "Town Hall ends at Monday, October 12 at 7:35 AM UTC."),
    ("which time zone is this event in?", "Town Hall's published time zone is Asia/Kolkata."),
    ("give me start time and end time of event in indian time",
     "Town Hall starts at Monday, October 12 at 11:35 AM IST and ends at Monday, October 12 at 1:05 PM IST."),
    ("what are the start and end times", "Town Hall starts at Monday, October 12 at 11:35 AM IST and ends at"),
    ("end time in EST", "Town Hall ends at Monday, October 12 at 3:35 AM EDT."),
    ("is chat enabled?", "Chat is on for this event."),
    ("are questions allowed", "Q&A is off for this event."),
    ("what category is this event", "Town Hall is listed under the category: Webinar."),
])
def test_end_time_duration_host_and_details_are_answered_when_the_context_holds_them(question, expected):
    reply = event_answers.answer(question, FULL)
    assert reply.grounded and reply.text.startswith(expected)


def test_time_left_while_live_and_after_the_end():
    live = {**FULL, "public_end_at": "2099-01-01T00:00:00Z"}
    assert event_answers.answer("how much time is left?", live).text.startswith("About ")
    ended = {**FULL, "public_status": "ended"}
    assert event_answers.answer("how much time is left?", ended).text == "Town Hall has ended."


@pytest.mark.parametrize("question", [
    "duriation of this event", "how long is it?", "when does it end?", "how much time is left?",
    "who is hosting this event?", "is the host john speaking right now?",
    "what's the agenda?", "give me the event description", "who are the speakers?",
    "at which time meeting will end", "what time does the session finish",
    "when will the meeting be over",
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
        **FULL, "duration_minutes": 120, "attendee_count": 400, "join_url": "https://x",
        "features": {"chat": True}}))
    assert set(v.safe_claims) == set(FULL)


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
