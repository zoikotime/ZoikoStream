"""Confirmation integrity (§6.11, §10, §15): nothing changes before an explicit confirmation,
confirmation is single-use and idempotent, stale proposals are withdrawn, and an unknown
outcome is reconciled against authoritative state instead of being guessed."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.event import Event, EventAccessLink
from app.models.support_ticket import SupportTicket
from app.operator_guide import actions
from app.operator_guide.schema import AuditEvent, Proposal
from app.operator_guide.sources import now
from conftest import BASE, blocks_of


def _proposal(reply) -> dict:
    found = blocks_of(reply, "proposal")
    assert len(found) == 1, reply
    return found[0]


def _confirm(client, headers, proposal_id, key="key-0001-abcdef"):
    return client.post(f"{BASE}/proposals/{proposal_id}/confirm",
                       headers={**headers, "Idempotency-Key": key})


@pytest.fixture
def setup(world):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin, title="Quarterly Town Hall", chat_enabled=True)
    return org, admin, ev, world.headers(admin)


def test_proposal_names_the_change_and_changes_nothing(client, db, ask, setup):
    _, _, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    assert p["confirm_label"] == "Confirm: Turn off chat"
    assert p["risk"] == "R3" and p["status"] == "proposed"
    assert p["change"] == {"field": "chat_enabled", "label": "Chat", "from": "On", "to": "Off"}
    db.expire_all()
    assert db.get(Event, ev.id).chat_enabled is True


def test_confirm_executes_through_platform_and_returns_verified_receipt(client, db, ask, setup):
    _, admin, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    res = _confirm(client, h, p["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "succeeded"
    assert body["receipt"]["verified"] is True
    assert body["receipt"]["message"] == "Chat changed from On to Off."
    db.expire_all()
    assert db.get(Event, ev.id).chat_enabled is False
    # Audited with the confirming human and the correlation id.
    row = db.scalar(select(AuditEvent).where(AuditEvent.kind == "action",
                                             AuditEvent.correlation_id == body["receipt"]
                                             ["correlation_id"]))
    assert row.outcome == "succeeded" and row.confirmation_actor == admin.id


def test_receipt_is_appended_to_the_conversation(client, ask, setup):
    _, _, ev, h = setup
    reply = ask(h, "turn off chat", event_id=str(ev.id))
    _confirm(client, h, _proposal(reply)["id"])
    convo = client.get(f"{BASE}/conversations/{reply['conversation_id']}", headers=h).json()
    assert convo["messages"][-1]["intent"] == "action.receipt"
    assert convo["messages"][-1]["text"] == "Chat changed from On to Off."


def test_idempotency_key_is_required(client, ask, setup):
    _, _, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    res = client.post(f"{BASE}/proposals/{p['id']}/confirm", headers=h)
    assert res.status_code == 422 and res.json()["code"] == "OPGUIDE_VALIDATION_FAILED"


def test_replayed_key_returns_original_receipt_without_reexecuting(client, db, ask, setup):
    _, _, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    first = _confirm(client, h, p["id"], "replay-key-123").json()
    second = _confirm(client, h, p["id"], "replay-key-123").json()
    assert second.pop("replayed") is True and first == second
    assert db.scalar(select(AuditEvent).where(AuditEvent.kind == "action",
                                              AuditEvent.correlation_id ==
                                              first["receipt"]["correlation_id"])) is not None
    n = len(db.scalars(select(AuditEvent).where(
        AuditEvent.correlation_id == first["receipt"]["correlation_id"],
        AuditEvent.kind == "action")).all())
    assert n == 1


def test_second_confirmation_with_new_key_is_refused(client, ask, setup):
    _, _, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    assert _confirm(client, h, p["id"], "first-key-1").status_code == 200
    res = _confirm(client, h, p["id"], "second-key-2")
    assert res.status_code == 409


def test_key_reuse_across_proposals_is_refused(client, ask, setup):
    _, _, ev, h = setup
    a = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    assert _confirm(client, h, a["id"], "shared-key-1").status_code == 200
    b = _proposal(ask(h, "turn on Q&A", event_id=str(ev.id)))
    assert _confirm(client, h, b["id"], "shared-key-1").status_code == 409


def test_only_the_proposer_can_confirm(client, world, ask, setup):
    org, _, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    other = world.headers(world.user(org))
    assert _confirm(client, other, p["id"]).status_code == 404


def test_cancelled_and_expired_proposals_cannot_execute(client, db, ask, setup):
    _, _, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    assert client.post(f"{BASE}/proposals/{p['id']}/cancel", headers=h).json()["status"] == \
        "cancelled"
    assert _confirm(client, h, p["id"]).status_code == 409

    q = _proposal(ask(h, "turn on polls", event_id=str(ev.id)))
    row = db.get(Proposal, uuid.UUID(q["id"]))
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    res = _confirm(client, h, q["id"], "expired-key-1")
    assert res.status_code == 409 and "expired" in res.json()["message"]
    db.expire_all()
    assert db.get(Event, ev.id).polls_enabled is False


def test_stale_proposal_is_withdrawn_instead_of_overwriting(client, db, ask, setup):
    _, _, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    # Someone else changes the same setting in the console meanwhile.
    db.get(Event, ev.id).chat_enabled = False
    db.commit()
    res = _confirm(client, h, p["id"])
    assert res.status_code == 409 and "changed after I proposed" in res.json()["message"]


def test_no_proposal_when_already_in_requested_state(client, ask, setup):
    _, _, ev, h = setup
    reply = ask(h, "turn on chat", event_id=str(ev.id))
    assert not blocks_of(reply, "proposal")
    assert reply["message"]["text"] == "Chat is already on. Nothing to change."


def test_material_change_is_r4_and_lists_impact(client, db, ask, setup):
    _, _, ev, h = setup
    p = _proposal(ask(h, "make it private", event_id=str(ev.id)))
    assert p["risk"] == "R4" and p["confirm_label"] == "Confirm: Make event private"
    assert any("invited" in line for line in p["impact"])
    assert _confirm(client, h, p["id"]).json()["status"] == "succeeded"
    db.expire_all()
    assert db.get(Event, ev.id).visibility == "private"


def test_platform_refusal_is_reported_as_failed(client, db, ask, setup, monkeypatch):
    _, _, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    monkeypatch.setattr(actions, "asgi_executor", lambda app, **kw: (
        lambda *a: actions.PlatformResponse(403, {"detail": "You may only edit events you host"})))
    body = _confirm(client, h, p["id"]).json()
    assert body["status"] == "failed"
    assert body["receipt"]["message"] == ("The service did not confirm the change. No successful "
                                          "change has been recorded.")
    assert body["receipt"]["reason"] == "You may only edit events you host"
    db.expire_all()
    assert db.get(Event, ev.id).chat_enabled is True


def test_timeout_without_effect_is_unknown_never_success(client, db, ask, setup, monkeypatch):
    _, _, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    monkeypatch.setattr(actions, "asgi_executor", lambda app, **kw: (
        lambda *a: actions.PlatformResponse(None, error="timeout")))
    body = _confirm(client, h, p["id"]).json()
    assert body["status"] == "unknown"
    assert body["receipt"]["message"] == ("The request was submitted, but completion is not "
                                          "confirmed. Check current state before retrying.")


def test_timeout_after_effect_is_reconciled_to_success(client, db, ask, setup, monkeypatch):
    _, _, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))

    def applied_then_timed_out(*_):
        from app.db import SessionLocal
        with SessionLocal() as other:
            other.get(Event, ev.id).chat_enabled = False
            other.commit()
        return actions.PlatformResponse(None, error="timeout")
    monkeypatch.setattr(actions, "asgi_executor", lambda app, **kw: applied_then_timed_out)
    body = _confirm(client, h, p["id"]).json()
    assert body["status"] == "succeeded"
    assert body["receipt"]["reconciled"] is True and body["receipt"]["verified"] is True


def test_revoke_access_link_flow(client, db, world, ask, setup):
    org, admin, ev, h = setup
    link = EventAccessLink(event_id=ev.id, org_id=org.id, label="Press", token_hash="f" * 64,
                           created_by=admin.id)
    db.add(link)
    db.commit()
    reply = ask(h, "revoke the access link", event_id=str(ev.id))
    choice = blocks_of(reply, "choices")[0]
    assert choice["options"][0]["label"] == "Press"
    assert "f" * 64 not in str(reply)  # the stored hash is never shown
    reply = ask(h, "", event_id=str(ev.id), conversation_id=reply["conversation_id"],
                action=choice["options"][0]["value"])
    p = _proposal(reply)
    assert p["risk"] == "R4" and p["confirm_label"] == "Confirm: Revoke access link"
    assert _confirm(client, h, p["id"]).json()["status"] == "succeeded"
    db.expire_all()
    assert db.get(EventAccessLink, link.id).revoked_at is not None


def test_support_case_is_admin_only_and_opened_through_platform(client, db, world, ask, setup):
    org, admin, ev, h = setup
    host_reply = ask(world.headers(world.user(org, "host")),
                     "open a support case: viewers see a black screen after ten minutes")
    assert not blocks_of(host_reply, "proposal")
    assert "organization admin" in host_reply["message"]["text"]

    p = _proposal(ask(h, "open a support case: viewers see a black screen after ten minutes"))
    assert p["change"]["to"] == "viewers see a black screen after ten minutes"
    body = _confirm(client, h, p["id"]).json()
    assert body["status"] == "succeeded" and body["receipt"]["reference"]
    ticket = db.scalar(select(SupportTicket).where(SupportTicket.org_id == org.id))
    assert ticket.category == "live_event" and ticket.priority == "normal"


def test_structured_actions_are_limited_to_the_catalog(client, ask, setup):
    _, _, ev, h = setup
    reply = ask(h, "", event_id=str(ev.id), action={"action_id": "event.delete", "params": {}},
                status=422)
    assert reply["code"] == "OPGUIDE_VALIDATION_FAILED"


def test_reconciled_event_change_carries_no_case_reference(client, db, ask, setup, monkeypatch):
    _, _, ev, h = setup
    p = _proposal(ask(h, "make it private", event_id=str(ev.id)))

    def applied_then_failed(*_):
        from app.db import SessionLocal
        with SessionLocal() as other:
            other.get(Event, ev.id).visibility = "private"
            other.commit()
        return actions.PlatformResponse(502)
    monkeypatch.setattr(actions, "asgi_executor", lambda app, **kw: applied_then_failed)
    body = _confirm(client, h, p["id"]).json()
    assert body["status"] == "succeeded" and body["receipt"]["reconciled"] is True
    assert body["receipt"]["reference"] is None


# ── §6.7 permission basis · §14 receipt · §16 audit ─────────────────────────────────

def test_proposal_names_its_permission_basis(world, ask, setup):
    org, admin, ev, h = setup
    assert _proposal(ask(h, "turn off chat", event_id=str(ev.id)))["permission"] == \
        "Allowed: organization admin"
    host = world.user(org, "host")
    world.assign(ev, host)
    p = _proposal(ask(world.headers(host), "turn off chat", event_id=str(ev.id)))
    assert p["permission"] == "Allowed: you host this event"
    own = world.event(org, host, title="Host's Own")
    p = _proposal(ask(world.headers(host), "turn off chat", event_id=str(own.id)))
    assert p["permission"] == "Allowed: you created this event"


def test_receipt_names_actor_and_resulting_value(client, ask, setup):
    _, admin, ev, h = setup
    body = _confirm(client, h, _proposal(ask(h, "turn off chat", event_id=str(ev.id)))["id"]).json()
    receipt = body["receipt"]
    assert receipt["actor"] == {"id": str(admin.id), "name": admin.full_name}
    assert receipt["result"] == "Off"


def test_failed_receipt_has_no_resulting_value(client, ask, setup, monkeypatch):
    _, admin, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    monkeypatch.setattr(actions, "asgi_executor", lambda app, **kw: (
        lambda *a: actions.PlatformResponse(403, {"detail": "no"})))
    receipt = _confirm(client, h, p["id"]).json()["receipt"]
    assert receipt["result"] is None and receipt["actor"]["id"] == str(admin.id)


def test_timeout_is_marked_on_the_receipt_and_audit(client, db, ask, setup, monkeypatch):
    _, _, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    monkeypatch.setattr(actions, "asgi_executor", lambda app, **kw: (
        lambda *a: actions.PlatformResponse(None, error="timeout")))
    receipt = _confirm(client, h, p["id"]).json()["receipt"]
    assert receipt["timeout"] is True and receipt["error_code"] == "OPGUIDE_TIMEOUT"
    row = db.scalar(select(AuditEvent).where(AuditEvent.correlation_id == receipt["correlation_id"],
                                             AuditEvent.kind == "action"))
    assert row.outcome == "unknown_timeout"


def test_message_audit_links_to_the_proposal_it_created(db, ask, setup):
    _, _, ev, h = setup
    reply = ask(h, "turn off chat", event_id=str(ev.id))
    p = _proposal(reply)
    row = db.scalar(select(AuditEvent).where(
        AuditEvent.kind == "message",
        AuditEvent.final_response_ref == reply["message"]["id"]))
    assert row.correlation_id == p["correlation_id"]
    proposal_row = db.scalar(select(AuditEvent).where(AuditEvent.kind == "proposal",
                                                      AuditEvent.final_response_ref == p["id"]))
    assert proposal_row.correlation_id == row.correlation_id


def test_ttl_expiry_is_audited_when_touched(client, db, ask, setup):
    _, admin, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    row = db.get(Proposal, uuid.UUID(p["id"]))
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert _confirm(client, h, p["id"], "ttl-key-0001").status_code == 409
    db.expire_all()
    audits = db.scalars(select(AuditEvent).where(AuditEvent.correlation_id == p["correlation_id"],
                                                 AuditEvent.outcome == "expired")).all()
    assert len(audits) == 1 and audits[0].actor_id == admin.id
    assert audits[0].authorization_result == "expired_ttl"


def test_ttl_expiry_is_audited_by_the_sweep_without_a_click(db, ask, setup):
    org, admin, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    row = db.get(Proposal, uuid.UUID(p["id"]))
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    # Any later message from the same user sweeps their expired proposals.
    ask(h, "what does conditional readiness mean")
    db.expire_all()
    assert db.get(Proposal, uuid.UUID(p["id"])).status == "expired"
    assert db.scalar(select(AuditEvent).where(AuditEvent.correlation_id == p["correlation_id"],
                                              AuditEvent.outcome == "expired"))
    # A global sweep never double-audits.
    assert actions.expire_due(db) == 0


def test_global_sweep_expires_other_users_proposals(db, world, ask, setup):
    org, admin, ev, h = setup
    p = _proposal(ask(h, "turn off chat", event_id=str(ev.id)))
    row = db.get(Proposal, uuid.UUID(p["id"]))
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert actions.expire_due(db) >= 1
    db.commit()
    audit_row = db.scalar(select(AuditEvent).where(AuditEvent.correlation_id ==
                                                   p["correlation_id"],
                                                   AuditEvent.outcome == "expired"))
    assert audit_row.role_context == "org_admin" and audit_row.org_id == org.id
