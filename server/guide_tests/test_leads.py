"""Consent, lead flow, contact-flow adapter, handoff, feedback, purge
(API §11–14; Conversation Flow §8; API-AT-11..13, CF-AT-09..13, DB-AT-08/10/13/14)."""
import uuid

import httpx
import pytest
from sqlalchemy import func, select, text

from app.guide import adapters, crypto
from app.guide.adapters import AdapterOutcome, ContactFlowAdapter
from app.guide.models import (
    GuideConsent, GuideExternalOperation, GuideFeedback, GuideLeadFieldValue, GuideSession,
)
from app.guide.purge import purge_once

VALID = {"full_name": "Jamie Rivera", "work_email": "jamie@example.org", "country_code": "us",
         "organization": "Example Org", "use_case": "memorials"}


class FakeAdapter:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes) or ["confirmed"]
        self.calls = []

    def submit(self, payload, *, visitor_ip):
        self.calls.append(payload)
        return AdapterOutcome(self.outcomes.pop(0) if len(self.outcomes) > 1
                              else self.outcomes[0])


def start(g, kind="demo"):
    res = g.post("/lead-flows", {"flow_kind": kind})
    assert res.status_code == 201, res.text
    return res.json()


def fill(g, flow, values):
    return g.patch(f"/lead-flows/{flow['flow_id']}", {"fields": values})


def consent(g, status="granted"):
    res = g.c.put(f"/api/v1/public-guide/sessions/{g.session_id}/consents/sales_contact",
                  json={"status": status, "notice_version": "sales-contact-v1"},
                  headers=g.headers())
    assert res.status_code == 200, res.text
    return res.json()


def submit(g, flow, receipt, key=None):
    return g.post(f"/lead-flows/{flow['flow_id']}/submit", {"consent_receipt_id": receipt},
                  headers={"Idempotency-Key": key or str(uuid.uuid4())})


@pytest.fixture
def fake():
    a = FakeAdapter("confirmed")
    adapters.set_lead_adapter(a)
    return a


# ── Flow & validation ──────────────────────────────────────────────────────────────────────

def test_purpose_notice_precedes_fields(guide, db):  # CF-AT-09
    flow = start(guide)
    assert flow["purpose_notice"]["text"].startswith("Tell us how to reach you")
    assert flow["purpose_notice"]["privacy_href"] == "/privacy"
    assert flow["consent"]["required"] is True
    assert [f["field_id"] for f in flow["fields"] if f["required"]] == [
        "full_name", "work_email", "country_code"]
    assert db.get(GuideSession, uuid.UUID(guide.session_id)).state == "LEAD_FLOW"
    assert start(guide)["flow_id"] == flow["flow_id"]  # resumes, never re-asks


def test_invalid_field_keeps_valid_ones(guide):  # CF-AT-10
    flow = start(guide)
    res = fill(guide, flow, {"full_name": "Jamie Rivera", "work_email": "not-an-email"})
    assert res.status_code == 422
    body = res.json()
    assert body["field_errors"] == [{"field": "work_email", "code": "invalid",
                                     "message": "Enter a valid email address."}]
    values = {f["field_id"]: f.get("value") for f in body["current"]["fields"]}
    assert values["full_name"] == "Jamie Rivera" and values["work_email"] is None


def test_secret_in_notes_is_refused(guide):
    flow = start(guide)
    res = fill(guide, flow, {"notes": "my event password: hunter22"})
    assert res.status_code == 422 and res.json()["field_errors"][0]["field"] == "notes"


def test_unknown_field_rejected(guide):
    flow = start(guide)
    res = fill(guide, flow, {"ssn": "123"})
    assert res.status_code == 422 and res.json()["field_errors"][0]["code"] == "unknown_field"


def test_values_are_ciphertext_only(guide, db):  # DB-AT-10
    flow = start(guide)
    assert fill(guide, flow, VALID).status_code == 200
    dump = db.execute(text("SELECT string_agg(encode(value_ciphertext, 'escape'), ' ') "
                           "FROM guide_lead_field_values")).scalar()
    assert "jamie@example.org" not in dump


# ── Consent ────────────────────────────────────────────────────────────────────────────────

def test_consent_history_is_immutable(guide, db):  # DB-AT-08
    granted = consent(guide)
    assert consent(guide)["receipt_id"] == granted["receipt_id"]  # idempotent PUT
    withdrawn = consent(guide, "withdrawn")
    rows = db.scalars(select(GuideConsent).order_by(GuideConsent.recorded_at)).all()
    assert len(rows) == 2 and rows[1].supersedes_consent_id == rows[0].consent_id
    assert withdrawn["receipt_id"] != granted["receipt_id"]


def test_stale_notice_version_rejected(guide):
    res = guide.c.put(f"/api/v1/public-guide/sessions/{guide.session_id}/consents/sales_contact",
                      json={"status": "granted", "notice_version": "old"},
                      headers=guide.headers())
    assert res.status_code == 422


# ── Submission outcomes ────────────────────────────────────────────────────────────────────

def test_submit_without_consent_is_blocked(guide, fake):  # API-AT-11, CF-AT-11
    flow = start(guide)
    fill(guide, flow, VALID)
    res = submit(guide, flow, None)
    assert res.status_code == 422 and fake.calls == []


def test_withdrawn_consent_blocks_submit(guide, fake):
    flow = start(guide)
    fill(guide, flow, VALID)
    receipt = consent(guide)["receipt_id"]
    consent(guide, "withdrawn")
    assert submit(guide, flow, receipt).status_code == 422 and fake.calls == []


def test_missing_required_fields_blocked(guide, fake):
    flow = start(guide)
    fill(guide, flow, {"full_name": "Jamie Rivera"})
    res = submit(guide, flow, consent(guide)["receipt_id"])
    assert res.status_code == 422 and fake.calls == []


def test_confirmed_submission(guide, fake, db):  # CF-AT-12
    flow = start(guide)
    fill(guide, flow, {**VALID, "notes": "About 200 viewers."})
    res = submit(guide, flow, consent(guide)["receipt_id"])
    assert res.status_code == 200
    body = res.json()
    assert body["outcome"] == "confirmed" and body["submission_reference"].startswith("GD-")
    payload = fake.calls[0]
    assert payload == {"first": "Jamie", "last": "Rivera", "email": "jamie@example.org",
                       "org": "Example Org", "country": "US",
                       "topic": "Platform evaluation & enterprise sales",
                       "message": payload["message"]}
    assert "Use case: Memorial services" in payload["message"]
    assert "About 200 viewers." in payload["message"]
    db.expire_all()
    assert db.get(GuideSession, uuid.UUID(guide.session_id)).state == "SALES_HANDOFF"
    short = db.scalar(select(func.max(GuideLeadFieldValue.purge_after)))
    assert (short - db.scalar(select(func.now()))).days <= 7


def test_same_key_replays_without_second_send(guide, fake):  # API-AT-12
    flow = start(guide)
    fill(guide, flow, VALID)
    receipt = consent(guide)["receipt_id"]
    key = str(uuid.uuid4())
    first = submit(guide, flow, receipt, key).json()
    again = submit(guide, flow, receipt, key).json()
    assert len(fake.calls) == 1 and again["replayed"] is True
    assert again["submission_reference"] == first["submission_reference"]


def test_new_key_after_confirmed_is_conflict(guide, fake):
    flow = start(guide)
    fill(guide, flow, VALID)
    receipt = consent(guide)["receipt_id"]
    submit(guide, flow, receipt)
    assert submit(guide, flow, receipt).status_code == 409
    assert len(fake.calls) == 1


def test_failed_outcome_never_claims_success_and_allows_retry(guide):
    a = FakeAdapter("failed", "confirmed")
    adapters.set_lead_adapter(a)
    flow = start(guide)
    fill(guide, flow, VALID)
    receipt = consent(guide)["receipt_id"]
    failed = submit(guide, flow, receipt).json()
    assert failed["outcome"] == "failed" and failed["submission_reference"] is None
    assert "didn't go through" in failed["message"]
    assert submit(guide, flow, receipt).json()["outcome"] == "confirmed"


def test_unknown_outcome_is_504_and_blocks_blind_resubmit(guide, db):  # API-AT-13
    a = FakeAdapter("unknown")
    adapters.set_lead_adapter(a)
    flow = start(guide)
    fill(guide, flow, VALID)
    receipt = consent(guide)["receipt_id"]
    res = submit(guide, flow, receipt)
    assert res.status_code == 504 and res.json()["code"] == "GUIDE_OUTCOME_UNKNOWN"
    assert res.json()["outcome"] == "unknown"
    assert submit(guide, flow, receipt).status_code == 409  # new key: no blind retry
    assert len(a.calls) == 1
    assert db.scalar(select(GuideExternalOperation.outcome)) == "unknown"


def test_idempotency_key_required(guide, fake):
    flow = start(guide)
    fill(guide, flow, VALID)
    res = guide.post(f"/lead-flows/{flow['flow_id']}/submit",
                     {"consent_receipt_id": consent(guide)["receipt_id"]})
    assert res.status_code == 400 and fake.calls == []


def test_cancel_discards_values(guide, db):
    flow = start(guide)
    fill(guide, flow, VALID)
    res = guide.patch(f"/lead-flows/{flow['flow_id']}", {"cancel": True})
    assert res.json()["status"] == "cancelled"
    assert db.scalar(select(func.count()).select_from(GuideLeadFieldValue)) == 0


# ── Contact-flow adapter mapping ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("behavior,expected", [
    (lambda: httpx.Response(202, json={"received": True}), "confirmed"),
    (lambda: httpx.Response(422, json={}), "failed"),
    (lambda: httpx.Response(429, json={}), "failed"),
    ("connect", "failed"),
    ("timeout", "unknown"),
])
def test_contact_adapter_outcomes(monkeypatch, behavior, expected):
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen["headers"] = headers
        if behavior == "connect":
            raise httpx.ConnectError("refused")
        if behavior == "timeout":
            raise httpx.ReadTimeout("slow")
        return behavior()
    monkeypatch.setattr(httpx, "post", fake_post)
    out = ContactFlowAdapter().submit({"x": 1}, visitor_ip="203.0.113.9")
    assert out.outcome == expected
    assert seen["headers"]["X-Forwarded-For"] == "203.0.113.9"


# ── Handoff & feedback ─────────────────────────────────────────────────────────────────────

def test_handoff_is_route_only(guide):  # PR-027
    res = guide.post("/handoffs", {"target": "support"},
                     headers={"Idempotency-Key": str(uuid.uuid4())})
    body = res.json()
    assert body["outcome"] == "route_available"
    assert body["actions"][0]["href"] == "/contact"
    assert "ticket" not in body["message"].lower()


def test_handoff_requires_idempotency_key(guide):
    assert guide.post("/handoffs", {"target": "sales"}).status_code == 400


def test_feedback_is_idempotent_and_comment_encrypted(guide, db):  # DB-AT-13
    msg = guide.post("/messages", {"client_message_id": str(uuid.uuid4()),
                                   "text": "Show me the guest list"}).json()
    payload = {"client_feedback_id": str(uuid.uuid4()), "target_type": "answer",
               "target_id": msg["assistant_message"]["message_id"], "rating": "down",
               "reason_code": "not_helpful", "comment": "my pin is 9911 and it failed"}
    assert guide.post("/feedback", payload).status_code == 202
    assert guide.post("/feedback", payload).status_code == 202
    rows = db.scalars(select(GuideFeedback)).all()
    assert len(rows) == 1
    comment = crypto.decrypt(rows[0].comment_ciphertext, context="guide_feedback.comment")
    assert "9911" not in comment


def test_feedback_target_must_be_own_answer(guide):
    res = guide.post("/feedback", {"client_feedback_id": str(uuid.uuid4()),
                                   "target_type": "answer", "target_id": str(uuid.uuid4()),
                                   "rating": "up"})
    assert res.status_code == 404


# ── Purge ──────────────────────────────────────────────────────────────────────────────────

def test_purge_removes_expired_rows_in_order(guide, db):  # DB-AT-14/15
    flow = start(guide)
    fill(guide, flow, VALID)
    db.execute(text("UPDATE guide_lead_field_values SET purge_after = now() - interval '1 h'"))
    db.execute(text("UPDATE guide_sessions SET purge_after = now() - interval '1 h'"))
    db.commit()
    counts = purge_once()
    assert counts["lead_field_values"] == len(VALID) and counts["sessions"] == 1
    assert db.scalar(select(func.count()).select_from(GuideSession)) == 0
    # Audit survives the session (FK SET NULL) and records only counts.
    audit = db.execute(text("SELECT metadata FROM guide_audit_events WHERE event_type = "
                            "'retention_purge'")).scalar()
    assert audit["sessions"] == 1
