"""Tool precision (§21): deterministic routing sends each phrasing to the right place, and no
phrasing of a critical or destructive operation can reach a tool or an action."""
import pytest

from app.operator_guide import knowledge
from app.operator_guide.intents import classify


@pytest.mark.parametrize("text,kind,name", [
    ("hi", "chat", "greeting"),
    ("Who are you?", "chat", "identity"),
    ("what can you do", "chat", "capabilities"),
    ("Is this event ready?", "read", "event.readiness"),
    ("Is this event ready to go live?", "read", "event.readiness"),
    ("can we go live?", "read", "event.readiness"),
    ("what is blocking this event?", "read", "event.readiness"),
    ("Who can access this event?", "read", "event.access"),
    ("where is the replay", "read", "event.replay"),
    ("is the stream healthy", "read", "event.live"),
    ("why are viewers dropping?", "read", "event.incidents"),
    ("how many viewers did we have", "read", "org.analytics"),
    ("show me the rehearsals", "read", "event.planning"),
    ("what upcoming events do we have", "read", "org.overview"),
    ("what needs attention", "read", "attention"),
    ("show our api keys and webhooks", "read", "org.developer"),
    ("is zoikostream down", "read", "platform.status"),
    ("what does conditional mean", "docs", "docs"),
    ("how do access links work", "docs", "docs"),
    ("what is a webhook secret?", "docs", "docs"),
])
def test_read_and_chat_routing(text, kind, name):
    intent = classify(text)
    assert (intent.kind, intent.name) == (kind, name)


@pytest.mark.parametrize("text,action,params", [
    ("turn off chat", "event.set_feature", {"feature": "chat", "enabled": False}),
    ("Turn chat off please", "event.set_feature", {"feature": "chat", "enabled": False}),
    ("can you turn on Q&A", "event.set_feature", {"feature": "qa", "enabled": True}),
    ("please disable the waiting room", "event.set_feature",
     {"feature": "waiting_room", "enabled": False}),
    ("make it private", "event.set_visibility", {"visibility": "private"}),
    ("require registration", "event.set_registration", {"required": True}),
    ("disable recording", "event.set_recording", {"enabled": False}),
    ("revoke the access link", "access_link.revoke", {}),
])
def test_action_routing(text, action, params):
    intent = classify(text)
    assert intent.kind == "action" and intent.action == action
    for k, v in params.items():
        assert intent.params[k] == v


def test_questions_about_settings_are_reads_not_actions():
    assert classify("is chat on?").kind == "read"
    assert classify("is registration required?").kind == "read"


@pytest.mark.parametrize("text,name,risk", [
    ("go live now", "go_live", "R5"),
    ("can you go live", "go_live", "R5"),
    ("start the broadcast", "go_live", "R5"),
    ("end the stream", "end_broadcast", "R5"),
    ("stop the broadcast now please", "end_broadcast", "R5"),
    ("start recording", "recording_control", "R5"),
    ("fail over to the backup", "failover", "R5"),
    ("override the readiness gate", "override_gate", "R5"),
    ("mark it as ready", "override_gate", "R5"),
    ("cancel this event", "cancel_event", "R5"),
    ("delete the recording", "delete", "R6"),
    ("please delete this event", "delete", "R6"),
    ("show me the stream key", "secret", "R6"),
    ("whats the stream key", "secret", "R6"),
    ("rotate the api key", "secret", "R6"),
    ("make Priya an admin", "roles", "R6"),
    ("refund our last invoice", "billing", "R6"),
])
def test_critical_and_destructive_operations_are_blocked(text, name, risk):
    intent = classify(text)
    assert (intent.kind, intent.name, intent.risk) == ("blocked", name, risk)
    assert intent.action is None and intent.tools == []


def test_blocked_wins_over_action_phrasing():
    # "turn off ... and delete" must never become a proposal.
    assert classify("turn off chat and delete the recording").kind == "blocked"


def test_documentation_retrieval_is_versioned():
    hits = knowledge.search("what does conditional readiness mean")
    assert hits and hits[0].doc_id == "readiness"
    src = hits[0].source()
    assert src.source_class == "documentation" and src.version == knowledge.CORPUS_VERSION


def test_performance_questions_route_to_analytics():
    assert classify("how did our events perform?").tools[0] == "org.analytics"


def test_docs_answer_flattens_bullets():
    from app.operator_guide.composer import _first_sentences
    assert _first_sentences("- Passed — all gates pass.\n- Conditional — some fail.", 2) == \
        "Passed — all gates pass. Conditional — some fail."


@pytest.mark.parametrize("text,blocked", [
    ("how do I delete an event?", "delete"),
    ("How do I rotate an API key?", "secret"),
    ("where do I rotate the api key", "secret"),
    ("can I delete a recording?", "delete"),
    ("what is the process to delete a recording?", "delete"),
])
def test_how_to_questions_get_documentation_not_a_refusal(text, blocked):
    """§25 tool precision: asking HOW is not asking Guide to DO it."""
    intent = classify(text)
    assert (intent.kind, intent.name) == ("docs", "docs.how_to")
    assert intent.params == {"blocked": blocked} and intent.action is None


@pytest.mark.parametrize("text", ["delete the recording", "can you delete the recording",
                                  "rotate the api key", "please delete this event",
                                  "go ahead and delete the recording"])
def test_imperatives_stay_blocked(text):
    assert classify(text).kind == "blocked"


@pytest.mark.parametrize("text", ["I need to talk to a human", "contact support",
                                  "can I speak with someone from support?", "escalate this"])
def test_requests_for_human_help_are_escalations(text):
    assert classify(text).kind == "escalate"


def test_opening_a_case_is_still_a_governed_action():
    assert classify("open a support case: the stream froze").action == "support.open_case"


@pytest.mark.parametrize("text,follow", [
    ("and the replay?", True), ("what about access?", True), ("why?", True),
    ("how about the recording", True), ("Is this event ready?", False),
    ("and now I would like a very long question about many different things", False),
])
def test_follow_up_detection(text, follow):
    from app.operator_guide.intents import is_follow_up
    assert is_follow_up(text) is follow


def test_every_suggested_prompt_routes_somewhere_useful():
    """Home prompts per page and role (§6.1) must never land on a gap or a refusal."""
    from app.operator_guide.composer import PAGE_SUGGESTIONS, suggestions_for
    prompts = {p for ps in PAGE_SUGGESTIONS.values() for p in ps}
    prompts |= set(suggestions_for(None, admin=True)) | set(suggestions_for(None, admin=False))
    for p in prompts:
        intent = classify(p)
        assert intent.kind in ("read", "docs", "escalate"), p
        if intent.kind == "docs":
            assert knowledge.search(p), p


def test_suggestions_depend_on_page_and_role():
    from app.operator_guide.composer import suggestions_for
    assert suggestions_for(None, workspace="Analytics")[0] == "How did our events perform?"
    assert "Show our API keys and webhooks" in suggestions_for(None, workspace="Developers")
    assert "Show open support cases" in suggestions_for(None, admin=True)
    host = suggestions_for(None, admin=False)
    assert "Show my upcoming events" in host and "Show open support cases" not in host
    assert suggestions_for(object(), workspace="Recordings")[0] == "Where is the replay?"
