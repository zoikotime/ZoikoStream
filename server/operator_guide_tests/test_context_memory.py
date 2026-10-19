"""Context, memory and answer structure (§4.2, §6.3, §6.11, §6.15, §8, §13, §15, §25):
visible context changes, follow-ups that reuse the previous turn, incident timelines from
authoritative timestamps, escalation routes by role, labelled interpretation, developer
answers that carry API version and environment, how-to answers, and tool timeouts."""
from datetime import timedelta

from sqlalchemy import select

from app.models.commercial import EventIncident
from app.models.live import AnalyticsSnapshot, BroadcastSession
from app.operator_guide import llm, tools
from app.operator_guide.schema import Message
from app.operator_guide.sources import now
from conftest import BASE, blocks_of


def _live_event(world, db, *, role="org_admin", health="warn", incident=False, viewers=None):
    org = world.org()
    user = world.user(org, role)
    ev = world.event(org, user, title="Night Launch", status="live")
    started = now() - timedelta(minutes=20)
    db.add(BroadcastSession(event_id=ev.id, org_id=org.id, status="live", started_at=started,
                            health_level=health, health_changed_at=now() - timedelta(minutes=3),
                            health_issues="Packet loss on ingest"))
    for i, v in enumerate(viewers or [100]):
        db.add(AnalyticsSnapshot(event_id=ev.id, org_id=org.id, viewers=v,
                                 created_at=now() - timedelta(seconds=5 + i * 15)))
    if incident:
        db.add(EventIncident(event_id=ev.id, severity="sev2", operational_state="delayed",
                             opened_at=now() - timedelta(minutes=2),
                             delay_started_at=now() - timedelta(minutes=2),
                             next_update_at=now() + timedelta(minutes=15),
                             customer_summary="The start is delayed by a power issue"))
    db.commit()
    return org, user, ev


# ── §8 Timeline + §6.11 incident summary ─────────────────────────────────────────────

def test_incident_triage_has_timeline_of_recorded_timestamps(world, ask, db):
    _, admin, ev = _live_event(world, db, incident=True)
    reply = ask(world.headers(admin), "what's wrong with the stream?", event_id=str(ev.id))
    timeline = blocks_of(reply, "timeline")[0]
    texts = [i["text"] for i in timeline["items"]]
    assert texts[0] == "Broadcast started"
    assert {"Program health changed to degraded", "Incident SEV2 opened", "Start delayed",
            "Next update due (SEV2)"} <= set(texts)
    assert all(i["at"] for i in timeline["items"])
    assert [i["at"] for i in timeline["items"]] == sorted(i["at"] for i in timeline["items"])
    # Nothing is invented: no "ended" item for a broadcast that has not ended.
    assert "Broadcast ended" not in texts and "Event resumed" not in texts


def test_open_incident_is_summarised_from_its_record(world, ask, db):
    _, admin, ev = _live_event(world, db, incident=True)
    reply = ask(world.headers(admin), "what's wrong with the stream?", event_id=str(ev.id))
    text = reply["message"]["text"]
    assert text.startswith("Incident SEV2 is open (delayed).")
    assert "Customer summary: The start is delayed by a power issue." in text
    assert "Next update is due" in text
    card = [b for b in blocks_of(reply, "status") if b["title"] == "Open incident"][0]
    assert card["subtitle"].startswith("Incident ")


def test_no_timeline_items_without_timestamps(world, ask, db):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin, title="Quiet Event")
    reply = ask(world.headers(admin), "any problems?", event_id=str(ev.id))
    assert not blocks_of(reply, "timeline")


# ── §8 Escalation ────────────────────────────────────────────────────────────────────

def test_detected_problem_offers_admin_a_support_case_proposal_not_a_case(world, ask, db):
    from app.models.support_ticket import SupportTicket
    org, admin, ev = _live_event(world, db)
    reply = ask(world.headers(admin), "what's wrong with the stream?", event_id=str(ev.id))
    esc = blocks_of(reply, "escalation")[0]
    proposal = [r for r in esc["routes"] if r["kind"] == "proposal"][0]
    assert proposal["action_id"] == "support.open_case"
    assert "Night Launch" in proposal["params"]["text"]
    assert any(r.get("href") == "/organization/support" for r in esc["routes"])
    assert db.scalar(select(SupportTicket).where(SupportTicket.org_id == org.id)) is None
    assert not blocks_of(reply, "proposal")  # offered, never created


def test_host_escalation_points_to_their_org_admin(world, ask, db):
    _, host, ev = _live_event(world, db, role="host")
    world.assign(ev, host)
    reply = ask(world.headers(host), "I need to talk to a human", event_id=str(ev.id))
    assert reply["message"]["payload"]["intent"] == "escalate"
    esc = blocks_of(reply, "escalation")[0]
    kinds = {r["kind"] for r in esc["routes"]}
    assert "proposal" not in kinds and "contact" in kinds
    assert not any(r.get("href") == "/organization/support" for r in esc["routes"])
    assert "organization admin" in reply["message"]["text"]


def test_stable_event_has_no_escalation(world, ask, db):
    _, admin, ev = _live_event(world, db, health="ok")
    reply = ask(world.headers(admin), "any problems?", event_id=str(ev.id))
    assert not blocks_of(reply, "escalation")


def test_escalation_case_proposal_needs_confirmation(client, world, ask, db):
    _, admin, ev = _live_event(world, db)
    h = world.headers(admin)
    reply = ask(h, "what's wrong with the stream?", event_id=str(ev.id))
    route = [r for r in blocks_of(reply, "escalation")[0]["routes"] if r["kind"] == "proposal"][0]
    reply = ask(h, "", event_id=str(ev.id), conversation_id=reply["conversation_id"],
                action={"action_id": route["action_id"], "params": route["params"]})
    p = blocks_of(reply, "proposal")[0]
    assert p["status"] == "proposed" and p["confirm_label"] == "Confirm: Open support case"


# ── §6.3 / §10 R1: interpretation is labelled ────────────────────────────────────────

def test_suspected_inference_is_disclosed_as_an_inference_source(world, ask, db):
    _, admin, ev = _live_event(world, db, health="ok", viewers=[40, 90, 100, 110, 100])
    payload = ask(world.headers(admin), "why are viewers dropping?",
                  event_id=str(ev.id))["message"]["payload"]
    assert payload["inference"] is True
    inf = [s for s in payload["sources"] if s["class"] == "inference"]
    assert inf and inf[0]["label"] == "Guide interpretation"


def test_model_explanation_is_marked_as_interpretation(world, ask, monkeypatch):
    class Explains:
        release = "scripted"

        def generate(self, task, request, facts, history=None):
            return llm.ModelResult("ok", {"explanation": "Host assignment is the missing gate."},
                                   self.release)
    monkeypatch.setattr(llm, "provider", lambda: Explains())
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin, impact="unrepeatable")
    payload = ask(world.headers(admin), "Is this event ready?",
                  event_id=str(ev.id))["message"]["payload"]
    assert payload["explanation"] and payload["inference"] is True
    assert any(s["id"] == "inference:explanation" and s["version"] == "scripted"
               for s in payload["sources"])


def test_plain_facts_are_not_marked_as_interpretation(world, ask):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin)
    payload = ask(world.headers(admin), "Who can access this event?",
                  event_id=str(ev.id))["message"]["payload"]
    assert payload["inference"] is False
    assert not [s for s in payload["sources"] if s["class"] == "inference"]


# ── §6.15 Developer answers ──────────────────────────────────────────────────────────

def test_developer_docs_answer_carries_api_version_environment_and_link(world, ask):
    org = world.org()
    reply = ask(world.headers(world.user(org)), "how do webhooks work?")
    payload = reply["message"]["payload"]
    assert payload["technical"]["api"] == "v1"
    assert payload["technical"]["environment"] == "Production"
    docs = blocks_of(reply, "docs")[0]
    assert docs["technical"]["label"] == "Environment: Production · API: v1"
    assert {"type": "navigate", "label": "Open developer platform",
            "href": "/organization/developers"} in payload["actions"]


def test_developer_state_answer_carries_api_version(world, ask):
    org = world.org()
    reply = ask(world.headers(world.user(org)), "show our api keys and webhooks")
    assert reply["message"]["payload"]["technical"]["api"] == "v1"
    assert blocks_of(reply, "status")[0]["subtitle"] == "Environment: Production · API: v1"


def test_non_technical_answers_have_no_technical_context(world, ask):
    org = world.org()
    reply = ask(world.headers(world.user(org)), "what does conditional readiness mean")
    assert reply["message"]["payload"]["technical"] is None


# ── §25 how-to vs imperative ─────────────────────────────────────────────────────────

def test_how_to_rotate_a_key_is_answered_from_docs(world, ask):
    org = world.org()
    reply = ask(world.headers(world.user(org)), "How do I rotate an API key?")
    payload = reply["message"]["payload"]
    assert payload["intent"] == "docs.how_to"
    assert blocks_of(reply, "docs")[0]["items"][0]["source_id"].startswith("doc:developer")
    note = blocks_of(reply, "info")[0]
    assert note["text"].startswith("Guide can't do this for you.")
    assert not blocks_of(reply, "blocker")


def test_how_to_delete_an_event_says_where_and_does_not_block(world, ask):
    org = world.org()
    reply = ask(world.headers(world.user(org)), "how do I delete an event?")
    assert reply["message"]["payload"]["intent"] == "docs.how_to"
    assert "dedicated delete workflow" in str(reply["message"]["payload"]["blocks"])
    assert not blocks_of(reply, "blocker")


def test_imperative_delete_stays_blocked(world, ask):
    org = world.org()
    reply = ask(world.headers(world.user(org)), "delete the recording")
    assert reply["message"]["payload"]["intent"] == "blocked.delete"
    assert blocks_of(reply, "blocker")[0]["rule"].startswith("R6")


# ── §4.2 visible context changes ─────────────────────────────────────────────────────

def test_switching_event_adds_a_visible_thread_notice(client, world, ask):
    org = world.org()
    admin = world.user(org)
    a = world.event(org, admin, title="Alpha Summit")
    b = world.event(org, admin, title="Beta Forum")
    h = world.headers(admin)
    first = ask(h, "Is this event ready?", event_id=str(a.id))
    assert first["context_notice"] is None
    second = ask(h, "Is this event ready?", event_id=str(b.id),
                 conversation_id=first["conversation_id"])
    assert second["context_notice"]["text"] == "Context: now Beta Forum"
    convo = client.get(f"{BASE}/conversations/{first['conversation_id']}", headers=h).json()
    intents = [m["intent"] for m in convo["messages"]]
    # The notice sits between the two turns, before the question it applies to.
    assert intents.index("context.change") == 2 and convo["messages"][3]["role"] == "user"


def test_named_event_switches_context_and_persists(world, ask):
    org = world.org()
    admin = world.user(org)
    a = world.event(org, admin, title="Alpha Summit")
    world.event(org, admin, title="Gamma Gala")
    h = world.headers(admin)
    first = ask(h, "Is this event ready?", event_id=str(a.id))
    second = ask(h, "Is Gamma Gala ready?", event_id=str(a.id),
                 conversation_id=first["conversation_id"])
    assert second["context"]["event"]["title"] == "Gamma Gala"
    assert second["context_notice"]["text"] == "Context: now Gamma Gala"


def test_event_title_matches_on_word_boundaries_only(world, ask):
    org = world.org()
    admin = world.user(org)
    world.event(org, admin, title="Test")
    reply = ask(world.headers(admin), "what is the latest readiness?")
    assert reply["context"]["event"] is None


# ── §13 conversation memory ──────────────────────────────────────────────────────────

def test_follow_up_reuses_previous_event(world, ask):
    org = world.org()
    admin = world.user(org)
    world.event(org, admin, title="Delta Day")
    h = world.headers(admin)
    first = ask(h, "Is Delta Day ready?")
    follow = ask(h, "and the replay?", conversation_id=first["conversation_id"])
    assert follow["message"]["payload"]["intent"] == "event.replay"
    assert follow["context"]["event"]["title"] == "Delta Day"
    assert blocks_of(follow, "status")[0]["title"] == "Replay · Delta Day"
    assert follow["context_notice"] is None


def test_topicless_follow_up_repeats_previous_read(world, ask):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin, title="Echo Expo")
    h = world.headers(admin)
    first = ask(h, "Who can access this event?", event_id=str(ev.id))
    follow = ask(h, "why?", event_id=str(ev.id), conversation_id=first["conversation_id"])
    assert follow["message"]["payload"]["intent"] == "event.access"


def test_follow_up_without_conversation_is_not_guessed(world, ask):
    org = world.org()
    admin = world.user(org)
    world.event(org, admin, title="Foxtrot Fair")
    reply = ask(world.headers(admin), "and the replay?")
    assert blocks_of(reply, "choices")  # asks which event instead of guessing


def test_recent_history_is_passed_to_the_explanation_model(world, ask, monkeypatch):
    seen = []

    class Recorder:
        release = "recorder"

        def generate(self, task, request, facts, history=None):
            seen.append(history or [])
            return llm.ModelResult("disabled", release=self.release)
    monkeypatch.setattr(llm, "provider", lambda: Recorder())
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin)
    h = world.headers(admin)
    first = ask(h, "Who can access this event?", event_id=str(ev.id))
    ask(h, "Is this event ready?", event_id=str(ev.id), conversation_id=first["conversation_id"])
    last = seen[-1]
    assert [t["role"] for t in last] == ["user", "guide"]
    assert last[0]["text"] == "Who can access this event?"


def test_context_notices_are_not_sent_to_the_model_as_history(db, world, ask):
    from app.operator_guide import memory
    from app.operator_guide.schema import Conversation
    org = world.org()
    admin = world.user(org)
    a = world.event(org, admin, title="Golf Gathering")
    b = world.event(org, admin, title="Hotel Huddle")
    h = world.headers(admin)
    first = ask(h, "Is this event ready?", event_id=str(a.id))
    ask(h, "Is this event ready?", event_id=str(b.id), conversation_id=first["conversation_id"])
    conv = db.scalar(select(Conversation).where(Conversation.org_id == org.id))
    assert all(not t["text"].startswith("Context:") for t in memory.history(db, conv))
    assert db.scalar(select(Message).where(Message.intent == "context.change",
                                           Message.conversation_id == conv.id))


# ── §14/§15 tool timeout ─────────────────────────────────────────────────────────────

def test_slow_tool_times_out_with_typed_error_and_request_survives(world, ask, monkeypatch):
    from sqlalchemy import text as sql
    monkeypatch.setenv("OPGUIDE_TOOL_TIMEOUT_SECONDS", "0.2")
    original = tools.REGISTRY["event.access"]

    def slow(db, actor, ctx):
        db.execute(sql("SELECT pg_sleep(2)"))
        return original.handler(db, actor, ctx)
    monkeypatch.setitem(tools.REGISTRY, "event.access", original.__class__(
        original.id, original.title, original.owner, original.risk, original.capability,
        original.needs_event, slow))
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin)
    reply = ask(world.headers(admin), "Who can access this event?", event_id=str(ev.id))
    assert reply["message"]["text"] == ("Access policy service did not respond in time. Nothing "
                                        "was changed; try again in a moment.")
    assert blocks_of(reply, "warning")[0]["rule"] == "Tool timeout"


def test_timeout_error_class_is_typed():
    from app.operator_guide import errors
    exc = errors.Timeout(errors.timed_out("Broadcast service"))
    assert exc.status == 504 and exc.body()["code"] == "OPGUIDE_TIMEOUT"


def test_missing_telemetry_uses_the_no_data_wording(world, ask, db):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin, title="Silent Stream", status="live")
    db.add(BroadcastSession(event_id=ev.id, org_id=org.id, status="live",
                            started_at=now() - timedelta(minutes=1), health_level="ok"))
    db.commit()
    reply = ask(world.headers(admin), "is the stream healthy", event_id=str(ev.id))
    rows = {r["label"]: r for r in blocks_of(reply, "status")[0]["rows"]}
    assert rows["Audience"]["detail"] == ("I do not have current audience telemetry for this "
                                          "session.")
