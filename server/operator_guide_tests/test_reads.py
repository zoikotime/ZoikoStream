"""Read answers (§7, §8, §13): readiness from the readiness service, sources and freshness on
every fact, stale telemetry never described as current, triage labels, documentation answers
with versions, and honest gaps."""
from datetime import timedelta

from sqlalchemy import select

from app.models.event import EventAccessLink
from app.models.live import AnalyticsSnapshot, BroadcastSession
from app.operator_guide.sources import now
from conftest import blocks_of


def _event_with_live_session(world, db, *, health="ok", snapshot_age=5, viewers=None):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin, title="Live Keynote", status="live")
    db.add(BroadcastSession(event_id=ev.id, org_id=org.id, status="live",
                            started_at=now() - timedelta(minutes=12), health_level=health,
                            health_changed_at=now() - timedelta(seconds=10)))
    for i, v in enumerate(viewers or [120]):
        db.add(AnalyticsSnapshot(event_id=ev.id, org_id=org.id, viewers=v,
                                 created_at=now() - timedelta(seconds=snapshot_age + i * 15)))
    db.commit()
    return admin, ev


def test_readiness_answers_directly_and_cannot_change_state(world, ask):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin, impact="unrepeatable")
    reply = ask(world.headers(admin), "Is this event ready?", event_id=str(ev.id))
    assert reply["message"]["text"].startswith("Not yet.")
    card = blocks_of(reply, "readiness")[0]
    assert card["overall"]["label"] == "BLOCKED"
    assert "cannot change it" in card["note"]
    blocked = [r for r in card["rows"] if r["state"] == "blocked"]
    assert {r["label"] for r in blocked} >= {"Host assigned", "Recording enabled"}
    sources = reply["message"]["payload"]["sources"]
    assert any(s["class"] == "authoritative" and s["owner"] == "Readiness service"
               for s in sources)


def test_ready_event_says_yes(world, ask, db):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin, recording_enabled=True)
    world.assign(ev, admin)
    reply = ask(world.headers(admin), "Is this event ready?", event_id=str(ev.id))
    assert reply["message"]["text"].startswith("Yes")
    assert blocks_of(reply, "readiness")[0]["overall"]["state"] == "ready"


def test_event_named_in_message_is_resolved_within_tenant(world, ask):
    org = world.org()
    admin = world.user(org)
    world.event(org, admin, title="Spring Gala")
    reply = ask(world.headers(admin), "Is Spring Gala ready?")
    assert reply["context"]["event"]["title"] == "Spring Gala"
    assert blocks_of(reply, "readiness")


def test_event_question_without_event_asks_which(world, ask):
    org = world.org()
    admin = world.user(org)
    world.event(org, admin, title="Only Event")
    reply = ask(world.headers(admin), "Is this event ready?")
    choice = blocks_of(reply, "choices")[0]
    assert choice["kind"] == "event" and choice["options"][0]["label"] == "Only Event"


def test_access_answer_never_contains_link_secrets(world, ask, db):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin, visibility="private")
    db.add(EventAccessLink(event_id=ev.id, org_id=org.id, label="Board",
                           token_hash="a1b2" * 16, expires_at=now() + timedelta(hours=3)))
    db.commit()
    reply = ask(world.headers(admin), "Who can access this event?", event_id=str(ev.id))
    raw = str(reply)
    assert "a1b2" * 16 not in raw
    rows = {r["label"]: r for r in blocks_of(reply, "status")[0]["rows"]}
    assert rows["Active access links"]["value"] == "1"
    assert "expire within 24 hours" in rows["Active access links"]["detail"]
    assert any("never shown" in b["text"] for b in blocks_of(reply, "info"))


def test_live_health_uses_live_mode(world, ask, db):
    admin, ev = _event_with_live_session(world, db)
    reply = ask(world.headers(admin), "is the stream healthy", event_id=str(ev.id))
    payload = reply["message"]["payload"]
    assert payload["mode"] == "live" and payload["suggestions"] == []
    assert payload["explanation"] is None
    rows = {r["label"]: r for r in blocks_of(reply, "status")[0]["rows"]}
    assert rows["Audience"]["value"] == "120 watching"


def test_stale_telemetry_is_never_described_as_current(world, ask, db):
    admin, ev = _event_with_live_session(world, db, snapshot_age=600)
    reply = ask(world.headers(admin), "is the stream healthy", event_id=str(ev.id))
    rows = {r["label"]: r for r in blocks_of(reply, "status")[0]["rows"]}
    assert rows["Audience"]["state"] == "unknown"
    assert "I will not describe it as current" in rows["Audience"]["detail"]
    assert reply["message"]["payload"]["stale"] is True
    tele = [s for s in reply["message"]["payload"]["sources"] if s["class"] == "operational"]
    assert tele and tele[0]["stale"] is True


def test_viewer_drop_is_only_suspected(world, ask, db):
    admin, ev = _event_with_live_session(world, db, viewers=[40, 90, 100, 110, 100])
    reply = ask(world.headers(admin), "why are viewers dropping?", event_id=str(ev.id))
    diag = blocks_of(reply, "diagnostic")[0]
    rows = {r["label"]: r for r in diag["rows"]}
    assert rows["Audience impact"]["state"] == "suspected"
    assert rows["Event incident"]["state"] == "stable"
    assert "suspicion" in diag["assessment"] and "not a confirmed issue" in diag["assessment"]


def test_degraded_program_is_detected_not_confirmed(world, ask, db):
    admin, ev = _event_with_live_session(world, db, health="warn")
    reply = ask(world.headers(admin), "what's wrong with the stream?", event_id=str(ev.id))
    diag = blocks_of(reply, "diagnostic")[0]
    assert {r["label"]: r["state"] for r in diag["rows"]}["Program health"] == "detected"
    assert "No platform incident is confirmed" in diag["assessment"]


def test_documentation_answer_cites_versioned_source(world, ask):
    org = world.org()
    reply = ask(world.headers(world.user(org)), "what does conditional readiness mean")
    docs = blocks_of(reply, "docs")[0]
    assert docs["items"][0]["source_id"].startswith("doc:readiness")
    src = reply["message"]["payload"]["sources"][0]
    assert src["class"] == "documentation" and src["version"] == "2026.10"


def test_unknown_question_is_an_honest_gap(world, ask):
    org = world.org()
    reply = ask(world.headers(world.user(org)), "what is the airspeed of an unladen swallow")
    assert reply["message"]["text"].startswith("I don't have documentation or data")
    assert reply["message"]["payload"]["sources"] == []


def test_overview_and_attention(world, ask):
    org = world.org()
    admin = world.user(org)
    world.event(org, admin, title="Blocked Soon", impact="unrepeatable", recording_enabled=False)
    h = world.headers(admin)
    overview = ask(h, "what upcoming events do we have")
    assert "upcoming event" in overview["message"]["text"]
    attention = ask(h, "what needs attention")
    items = blocks_of(attention, "attention")[0]["items"]
    assert any(i["kind"] == "readiness" and i["event"]["title"] == "Blocked Soon"
               for i in items)


def test_attention_for_hosts_is_limited_to_their_events(client, world):
    from conftest import BASE
    org = world.org()
    admin = world.user(org)
    host = world.user(org, "host")
    world.event(org, admin, title="Not Mine", impact="unrepeatable")
    mine = world.event(org, host, title="Mine", impact="unrepeatable")
    res = client.get(f"{BASE}/attention", headers=world.headers(host)).json()
    titles = {i["event"]["title"] for i in res["items"] if i["event"]}
    assert titles == {"Mine"} and mine


def test_identity_and_capabilities(world, ask):
    h = world.headers(world.user(world.org()))
    assert ask(h, "who are you?")["message"]["text"].startswith("I'm ZoikoStream Guide")
    caps = ask(h, "what can you do")["message"]["text"]
    assert "Go Live stay in the host console" in caps


def test_answers_are_audited_without_raw_text(world, ask, db):
    from app.operator_guide.schema import AuditEvent
    org = world.org()
    admin = world.user(org)
    ask(world.headers(admin), "what does conditional readiness mean")
    row = db.scalar(select(AuditEvent).where(AuditEvent.org_id == org.id,
                                             AuditEvent.kind == "message"))
    assert row.user_request_hash and len(row.user_request_hash) == 64
    assert "conditional" not in str(row.context) and row.policy_version == "opguide-policy-1.0"
    assert row.retrieval_source_refs and row.final_response_ref
