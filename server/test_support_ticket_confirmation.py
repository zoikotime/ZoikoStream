"""SUP-001 for a ticket Super Admin logs (POST /admin/support-tickets).

The report: a ticket logged from the console with a requester email was created, but the
requester never heard about it. Traced end to end, no email was ever ATTEMPTED - the admin route
inserted a bare SupportTicket row and returned. It never went through services/support_comms,
which is where a case gets its reference, its participants, and its SUP-001 "case opened"
notice. The customer-side route (open_case + notify_case_opened) did all three; this one did
none, so there was no recipient, no reference and no send.

These pin the fixed flow, and that the response says what actually happened to the email:
"sent" means the email service accepted it - not that it reached an inbox.

No real email leaves: the provider (httpx.post inside app.email) is replaced for every test,
by default with a stub that FAILS the test if anything tries to send.
"""
import logging
import re
import uuid

import httpx
import pytest
from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app.config import settings
from app.db import SessionLocal
from app.models import (
    Organization, SupportCaseParticipant, SupportNotice, SupportTicket, User,
)
from app.security import create_access_token, hash_password
from app.services import support_comms

REFERENCE = re.compile(r"^SUP-\d{4}-\d{6}$")


class Provider:
    """Stands in for Resend. `mode`: accept (200), refuse (403 from Resend), down (no answer)."""

    def __init__(self, mode="accept"):
        self.mode, self.calls = mode, []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append(json or {})
        request = httpx.Request("POST", url)
        if self.mode == "down":
            raise httpx.ConnectError("simulated Resend outage", request=request)
        if self.mode == "refuse":
            return httpx.Response(403, json={"message": "simulated refusal"}, request=request)
        return httpx.Response(200, json={"id": "simulated"}, request=request)


def _deny(url, headers=None, json=None, timeout=None):
    raise AssertionError(f"outbound email attempted: {(json or {}).get('subject')!r}")


@pytest.fixture(autouse=True)
def no_real_email(monkeypatch):
    # A non-empty key so _send gets as far as the (stubbed) provider, and a development
    # environment so the case link is allowed; individual tests override either.
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_not_a_real_key")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(email_mod.httpx, "post", _deny)


@pytest.fixture
def provider(monkeypatch):
    def install(mode="accept"):
        p = Provider(mode)
        monkeypatch.setattr(email_mod.httpx, "post", p)
        return p
    return install


@pytest.fixture
def w():
    db = SessionLocal()
    org = Organization(name=f"Sree inc. {uuid.uuid4().hex[:6]}", status="active")
    db.add(org)
    db.flush()
    admin = User(org_id=org.id, full_name="Platform Admin", role="super_admin", is_active=True,
                 email=f"sup-admin-{uuid.uuid4().hex[:10]}@example.com",
                 username=f"supadm{uuid.uuid4().hex[:10]}", password_hash=hash_password("x"),
                 email_verified=True)
    db.add(admin)
    db.commit()
    client = TestClient(m.app)
    client.headers.update({"Authorization": f"Bearer {create_access_token(admin, remember=False)}"})
    try:
        yield type("W", (), {"db": db, "org": org, "admin": admin, "client": client})
    finally:
        db.rollback()
        ids = [t.id for t in db.query(SupportTicket).filter(SupportTicket.org_id == org.id)]
        if ids:
            db.query(SupportNotice).filter(SupportNotice.ticket_id.in_(ids)).delete(synchronize_session=False)
            db.query(SupportCaseParticipant).filter(
                SupportCaseParticipant.ticket_id.in_(ids)).delete(synchronize_session=False)
            db.query(SupportTicket).filter(SupportTicket.id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id == admin.id).delete()
        db.query(Organization).filter(Organization.id == org.id).delete()
        db.commit()
        db.close()


def log_ticket(w, **overrides):
    body = {"org_id": str(w.org.id), "subject": "Can't start a stream",
            "message": "The encoder connects, then drops after ten seconds.",
            "priority": "high", "requester_email": "requester@example.com", **overrides}
    return w.client.post("/api/admin/support-tickets", json=body)


def tickets(w):
    w.db.expire_all()
    return w.db.query(SupportTicket).filter(SupportTicket.org_id == w.org.id).all()


# ── the requester is told ──────────────────────────────────────────────────────────────

def test_a_logged_ticket_confirms_to_the_requester(w, provider):
    p = provider("accept")
    r = log_ticket(w)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["confirmation"] == "sent"
    assert REFERENCE.match(body["case_reference"] or ""), body
    # One message, to exactly the address the admin typed, about exactly this case.
    assert len(p.calls) == 1
    sent = p.calls[0]
    assert sent["to"] == ["requester@example.com"]
    assert sent["subject"] == email_mod.SUP_001_SUBJECT.format(case=body["case_reference"])
    for fact in (body["case_reference"], "Can't start a stream", "High", w.org.name):
        assert fact in sent["text"], fact
    # The confirmation describes the case that was persisted, not a draft of it.
    [ticket] = tickets(w)
    assert ticket.case_reference == body["case_reference"]
    assert str(ticket.id) == body["id"]


def test_the_requester_is_the_case_participant(w, provider):
    provider("accept")
    log_ticket(w, requester_email="Requester@Example.com")
    [ticket] = tickets(w)
    people = support_comms.participants(w.db, ticket)
    assert [(x.email, x.role) for x in people] == [("requester@example.com", "requester")]


def test_without_a_requester_nothing_is_attempted(w):
    # The default stub fails the test on any send.
    r = log_ticket(w, requester_email=None)
    assert r.status_code == 201, r.text
    assert r.json()["confirmation"] == "not_requested"
    [ticket] = tickets(w)
    assert support_comms.participants(w.db, ticket) == []
    assert w.db.query(SupportNotice).filter(SupportNotice.ticket_id == ticket.id).count() == 0


# ── nothing is created, nothing is sent ────────────────────────────────────────────────

def test_an_invalid_requester_email_is_refused_before_anything_is_created(w):
    r = log_ticket(w, requester_email="not-an-email")
    assert r.status_code == 422
    assert tickets(w) == []


def test_an_unknown_organization_creates_and_sends_nothing(w):
    r = log_ticket(w, org_id=str(uuid.uuid4()))
    assert r.status_code == 404
    assert tickets(w) == []


# ── a failed email never undoes the ticket, and is never reported as sent ──────────────

@pytest.mark.parametrize("mode", ["refuse", "down"])
def test_a_provider_failure_keeps_the_ticket_and_says_so(w, provider, caplog, mode):
    provider(mode)
    with caplog.at_level(logging.INFO, logger="app.services.support_comms"):
        r = log_ticket(w)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["confirmation"] == "failed"
    assert [t.case_reference for t in tickets(w)] == [body["case_reference"]]
    # Diagnosable from the log: which case, what happened, why - and never the message body.
    line = next(rec.getMessage() for rec in caplog.records if "support_case_confirmation" in rec.getMessage())
    assert f"case={body['case_reference']}" in line
    assert "outcome=failed" in line and "failure=provider" in line
    assert "ten seconds" not in caplog.text


def test_unconfigured_email_is_reported_and_not_attempted(w, monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    r = log_ticket(w)
    assert r.status_code == 201, r.text
    assert r.json()["confirmation"] == "not_configured"
    [ticket] = tickets(w)
    # Nothing was attempted, so nothing is recorded as handled.
    assert w.db.query(SupportNotice).filter(SupportNotice.ticket_id == ticket.id).count() == 0


def test_an_unsafe_link_fails_the_email_not_the_ticket(w, monkeypatch):
    # Outside development an emailed link must be https and not localhost; building it raises.
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "APP_URL", "http://localhost:5173")
    r = log_ticket(w)
    assert r.status_code == 201, r.text
    assert r.json()["confirmation"] == "failed"
    assert len(tickets(w)) == 1


# ── retries ─────────────────────────────────────────────────────────────────────────────

def test_a_repeat_confirmation_is_not_sent_twice(w, provider):
    p = provider("accept")
    log_ticket(w)
    [ticket] = tickets(w)
    assert support_comms.confirm_logged_case(w.db, ticket) == "duplicate"
    assert len(p.calls) == 1


def test_a_deleted_case_does_not_block_the_next_reference(w, provider):
    # References were COUNT + 1. Super Admin can delete a ticket, and deleting any but the
    # newest made the count re-issue a reference a later case still held: the insert failed
    # the uniqueness index, and so did every case after it.
    provider("accept")
    refs = [log_ticket(w, subject=f"Case {i}").json()["case_reference"] for i in range(3)]
    middle = next(t for t in tickets(w) if t.case_reference == refs[1])
    assert w.client.delete(f"/api/admin/support-tickets/{middle.id}").status_code == 204
    r = log_ticket(w, subject="Case after a deletion")
    assert r.status_code == 201, r.text
    assert r.json()["case_reference"] not in refs


# ── deleting a case still works ────────────────────────────────────────────────────────
#
# A participant and a notice now hang off a logged ticket, as they always did off a case a
# customer opened. Neither has ON DELETE CASCADE, so DELETE /admin/support-tickets/{id} - which
# deleted only the ticket row - failed on every such case.

def _dependents(w, ticket_id):
    w.db.expire_all()
    return (w.db.query(SupportCaseParticipant).filter(SupportCaseParticipant.ticket_id == ticket_id).count()
            + w.db.query(SupportNotice).filter(SupportNotice.ticket_id == ticket_id).count())


def test_a_logged_case_with_a_requester_can_be_deleted(w, provider):
    provider("accept")
    ticket_id = log_ticket(w).json()["id"]
    assert _dependents(w, ticket_id) == 2          # the requester, and the SUP-001 notice
    assert w.client.delete(f"/api/admin/support-tickets/{ticket_id}").status_code == 204
    assert tickets(w) == [] and _dependents(w, ticket_id) == 0


def test_a_case_the_customer_opened_can_be_deleted(w, provider):
    provider("accept")
    ticket = support_comms.open_case(w.db, org=w.org, requester=w.admin, subject="Opened by the customer",
                                     description="x", category="technical")
    assert _dependents(w, ticket.id) == 1          # its requester
    assert w.client.delete(f"/api/admin/support-tickets/{ticket.id}").status_code == 204
    assert tickets(w) == []
