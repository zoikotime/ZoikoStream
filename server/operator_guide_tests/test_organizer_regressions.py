"""Regressions found by the organizer chatbot test run (2026-11-06): routing, a secret-rule false
positive, stale telemetry on the attention list, and an off-topic documentation hit."""
from datetime import timedelta

import pytest

from app.models.live import AnalyticsSnapshot, BroadcastSession
from app.operator_guide import intents
from app.operator_guide.sources import now


@pytest.mark.parametrize("text,intent", [
    ("How many viewers are watching right now?", "event.live"),
    ("What is the ingest status?", "event.live"),
    ("Which events are live right now?", "org.overview"),
    ("How many people registered?", "event.access"),
])
def test_routing(text, intent):
    assert intents.classify(text).name == intent


def test_ingest_definition_still_goes_to_docs():
    assert intents.classify("What does ingest status mean?").name != "event.live"


def test_retention_policy_is_documentation():
    i = intents.classify("How long are recordings retained?")
    assert i.kind == "docs"


def test_event_title_with_secret_is_not_a_secret_request():
    assert intents.classify("Tell me about Secret Santa").kind != "blocked"
    assert intents.classify("Show me the client secret").kind == "blocked"
    assert intents.classify("What is my API key?").kind == "blocked"


def test_live_answer_reports_current_viewers(world, ask, db):
    org = world.org(); admin = world.user(org)
    ev = world.event(org, admin, status="live")
    db.add(BroadcastSession(event_id=ev.id, org_id=org.id, status="live",
                            started_at=now() - timedelta(minutes=5), health_level="ok",
                            health_changed_at=now()))
    db.add(AnalyticsSnapshot(event_id=ev.id, org_id=org.id, viewers=77,
                             created_at=now() - timedelta(seconds=5)))
    db.commit()
    reply = ask(world.headers(admin), "How many viewers are watching right now?",
                event_id=str(ev.id))
    assert "77 watching now" in reply["message"]["text"]


def test_attention_flags_stale_live_telemetry(world, ask, db):
    org = world.org(); admin = world.user(org)
    ev = world.event(org, admin, title="Quiet Webinar", status="live")
    db.add(BroadcastSession(event_id=ev.id, org_id=org.id, status="live",
                            started_at=now() - timedelta(hours=1), health_level="ok",
                            health_changed_at=now() - timedelta(minutes=30)))
    db.add(AnalyticsSnapshot(event_id=ev.id, org_id=org.id, viewers=5,
                             created_at=now() - timedelta(minutes=20)))
    db.commit()
    reply = ask(world.headers(admin), "What needs my attention?")
    assert "Quiet Webinar" in str(reply["message"]["payload"]["blocks"])


def test_waiting_room_question_gets_waiting_room_docs(world, ask):
    org = world.org(); admin = world.user(org)
    reply = ask(world.headers(admin), "How does a waiting room work?")
    assert "waiting room" in reply["message"]["text"].lower()
    assert "support case" not in str(reply["message"]["payload"]["blocks"]).lower()
