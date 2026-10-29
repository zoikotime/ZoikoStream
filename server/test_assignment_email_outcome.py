"""BUG-009: host emails sent during event creation were not received, and nobody was told.

The assignment emails were queued as BackgroundTasks: sent after the response, every failure
swallowed into a log line (and on Cloud Run, run with CPU throttled). _set_role now sends them
inside the request and returns `email_sent` per assignee, so the console can say which did
not go out. These pin that contract without a database: every collaborator is replaced.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from app.routers import events as ev_router
from app.schemas.admin import AdminUserOut


def _user(name, **over):
    return SimpleNamespace(
        id=uuid.uuid4(), full_name=name, email=f"{name.lower()}@example.com",
        username=name.lower(), role="host", is_active=True, org_id=None, organization=None,
        created_at=None, staff_commercial_role=None, avatar_url=None, **over)


@pytest.fixture
def world(monkeypatch):
    admin = _user("Admin")
    admin.organization = SimpleNamespace(name="Acme")
    event = SimpleNamespace(id=uuid.uuid4(), title="Launch", start_time=None)
    state = {"previous": [], "current": []}
    sent = []

    monkeypatch.setattr(ev_router, "_get_event_or_404", lambda db, user, eid: event)
    monkeypatch.setattr(ev_router.crud, "valid_member_ids", lambda db, org, ids: set(ids))
    calls = {"n": 0}

    def list_assignees(db, event_id, role):
        calls["n"] += 1
        return state["previous"] if calls["n"] == 1 else state["current"]

    monkeypatch.setattr(ev_router.crud, "list_assignees", list_assignees)
    monkeypatch.setattr(ev_router.crud, "set_assignees", lambda db, ev, role, ids: None)
    monkeypatch.setattr(ev_router.event_audit, "record", lambda *a, **k: None)
    monkeypatch.setattr(ev_router.event_comms, "notify_team", lambda *a, **k: None)
    monkeypatch.setattr(ev_router, "_console_url", lambda role, eid: "https://app.test/host")
    monkeypatch.setattr(AdminUserOut, "model_validate", classmethod(
        lambda cls, u: cls(id=u.id, full_name=u.full_name, email=u.email, username=u.username,
                           role=u.role, is_active=u.is_active)))
    return SimpleNamespace(admin=admin, event=event, state=state, sent=sent)


def _run(world, people):
    background = BackgroundTasks()
    out = ev_router._set_role(None, world.admin, world.event.id, "host", [p.id for p in people], background)
    return {row.full_name: row.email_sent for row in out}, background


def test_sends_inside_the_request_and_reports_each_outcome(world, monkeypatch):
    nani, ravi = _user("Nani"), _user("Ravi")
    world.state["current"] = [nani, ravi]
    monkeypatch.setattr(ev_router, "send_assignment_email",
                        lambda to, *a: to != ravi.email)  # Ravi's is refused by the provider
    outcome, background = _run(world, [nani, ravi])
    assert outcome == {"Nani": True, "Ravi": False}
    # Nothing about the assignment emails is left for after the response.
    assert all(t.func.__name__ not in ("send_assignment_email", "send_self_host_confirmation_email")
               for t in background.tasks)


def test_one_crashing_send_does_not_lose_the_others(world, monkeypatch):
    nani, ravi = _user("Nani"), _user("Ravi")
    world.state["current"] = [nani, ravi]

    def send(to, *a):
        if to == nani.email:
            raise RuntimeError("template blew up")
        return True

    monkeypatch.setattr(ev_router, "send_assignment_email", send)
    outcome, _ = _run(world, [nani, ravi])
    assert outcome == {"Nani": False, "Ravi": True}


def test_self_assignment_gets_the_self_host_mail(world, monkeypatch):
    world.state["current"] = [world.admin]
    seen = []
    monkeypatch.setattr(ev_router, "send_self_host_confirmation_email",
                        lambda to, *a: seen.append(to) or True)
    monkeypatch.setattr(ev_router, "send_assignment_email",
                        lambda *a: pytest.fail("the creator must not get the 'you were assigned' mail"))
    outcome, _ = _run(world, [world.admin])
    assert seen == [world.admin.email]
    assert outcome == {"Admin": True}


def test_already_assigned_people_are_not_re_sent_and_report_none(world, monkeypatch):
    nani, ravi = _user("Nani"), _user("Ravi")
    world.state["previous"] = [nani]
    world.state["current"] = [nani, ravi]
    sent = []
    monkeypatch.setattr(ev_router, "send_assignment_email", lambda to, *a: sent.append(to) or True)
    outcome, _ = _run(world, [nani, ravi])
    assert sent == [ravi.email]
    assert outcome == {"Nani": None, "Ravi": True}


def test_email_sent_is_optional_on_every_other_response():
    row = AdminUserOut(id=uuid.uuid4(), full_name="A", email="a@example.com", username="a",
                       role="host", is_active=True)
    assert row.email_sent is None
