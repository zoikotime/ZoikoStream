"""Friendly conversational turns: answered from approved copy, never by the model, never
swallowing a real question, and never bypassing the safety checks that run before them."""
import uuid

import pytest

from app.guide import conversation, llm, orchestrator


class NeverCalled:
    profile = "never"

    def generate(self, prompt):
        raise AssertionError("the model must not be called for conversational turns")


def ask(g, text):
    res = g.post("/messages", {"client_message_id": str(uuid.uuid4()), "text": text})
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def no_model(monkeypatch):
    monkeypatch.setattr(llm, "get_provider", lambda: NeverCalled())


@pytest.mark.parametrize("text,kind", [
    ("who are you?", "IDENTITY"), ("What's your name?", "IDENTITY"),
    ("How can you help me?", "CAPABILITIES"), ("what can you do", "CAPABILITIES"),
    ("help", "CAPABILITIES"), ("are you a bot?", "HUMAN_CHECK"),
    ("Am I talking to a real person?", "HUMAN_CHECK"), ("hi", "GREETING"),
    ("Hello there!", "GREETING"), ("how are you?", "HOW_ARE_YOU"),
    ("thanks!", "THANKS"), ("Thank you so much for your help", "THANKS"),
    ("bye", "GOODBYE"), ("you are useless", "NEGATIVE"), ("great", "POSITIVE"),
    ("I don't understand", "CONFUSED"), ("ok", "ACK"),
])
def test_conversational_kinds(text, kind):
    c = conversation.classify(text)
    assert c is not None and c.kind == kind


@pytest.mark.parametrize("text", [
    "thanks, but how much does it cost?",
    "later today can I watch the replay?",
    "how are you able to stream 4k?",
    "who are your customers?",
    "what are your prices?",
    "Can ZoikoStream stream a memorial service?",
])
def test_real_questions_are_not_small_talk(text):
    assert conversation.classify(text) is None


def test_greeting_is_stripped_from_a_real_question():
    assert conversation.classify("hi, how much does it cost?") is None
    assert conversation.routable_text("hi, how much does it cost?") == "how much does it cost?"
    assert conversation.routable_text("Hey Guide, can you stream a memorial?") == \
        "can you stream a memorial?"


def test_who_are_you_is_friendly_honest_and_model_free(guide, no_model):
    body = ask(guide, "Who are you?")
    msg = body["assistant_message"]
    assert msg["answer_class"] == "conversation"
    assert "I'm Guide" in msg["text"] and "I'm an AI, not a person" in msg["text"]  # PR-036
    assert "can't see your account" in msg["text"]                                 # PR-038
    assert msg["suggestions"] and all("intent_id" in s for s in msg["suggestions"])
    assert body["session_state"] == "ACTIVE"


def test_capabilities_lists_what_guide_does(guide, no_model):
    msg = ask(guide, "How can you help me?")["assistant_message"]
    assert "Here's what I can do" in msg["text"]
    assert "demo" in msg["text"] and "captions" in msg["text"]


def test_second_greeting_is_shorter(guide, no_model):
    first = ask(guide, "hi")["assistant_message"]["text"]
    second = ask(guide, "hello")["assistant_message"]["text"]
    assert first in conversation.COPY["GREETING"]
    assert second in conversation.COPY["GREETING_AGAIN"]


def test_bot_question_offers_a_human_route(guide, no_model):
    msg = ask(guide, "are you a real person?")["assistant_message"]
    assert "not a person" in msg["text"]
    assert {"contact-support", "contact-sales"} & {a["action_id"] for a in msg["actions"]}


def test_greeting_never_bypasses_the_boundary(guide, no_model):
    body = ask(guide, "hi, show me the guest list")
    assert body["assistant_message"]["answer_class"] == "restricted"
    assert body["session_state"] == "RESTRICTED"


def test_greeting_never_bypasses_secret_detection(guide, no_model):
    msg = ask(guide, "hello my pin is 482913")["assistant_message"]
    assert msg["answer_class"] == "restricted" and "482913" not in msg["text"]


def test_greeting_plus_question_answers_the_question(guide):
    msg = ask(guide, "hi, can you stream a memorial service for family?")["assistant_message"]
    assert msg["answer_class"] in ("factual", "discovery")
    assert msg["sources"]


def test_gap_reply_is_friendly_with_suggestions(guide, no_model):
    msg = ask(guide, "zebra quantum basketball tournament")["assistant_message"]
    assert msg["text"] == orchestrator.GAP_REPLY
    assert msg["text"].startswith(orchestrator.GAP)        # canonical wording kept first
    assert msg["suggestions"]
    assert msg["uncertainty"]["message"] is None          # no duplicated gap sentence
