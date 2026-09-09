"""SUP-001 -> SUP-004 - support case lifecycle (ZST-EC-001).

The assertions the spec calls out as most important each have a dedicated test:

    SUP-001  customers can open their own cases   test_authorized_customer_can_open_a_case
    SUP-001  tenant isolation holds               test_customer_cannot_reach_another_tenant
    SUP-002  internal notes never leak            test_internal_notes_never_reach_the_customer
    SUP-002  waiting cases do not stall silently  test_action_required_and_reminder
    SUP-003  escalation uses real state           test_escalation_uses_real_state
    SUP-003  no invented next-update              test_next_update_is_never_invented
    SUP-004  sensitive cases get no survey        test_feedback_excluded_for_sensitive_cases
    ALL      support is not marketing consent     test_feedback_creates_no_marketing_consent

Run with `python test_support_comms.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.db import SessionLocal
from app.models import (
    CASE_CATEGORIES,
    FEEDBACK_EXCLUDED_SENSITIVITIES,
    Incident,
    Organization,
    SupportCaseParticipant,
    SupportEscalation,
    SupportNotice,
    SupportTicket,
    User,
)
from app.security import hash_password
from app.services import support_comms as sc

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"

OPENED = email_mod.SUP_001_SUBJECT
UPDATE = email_mod.SUP_002_UPDATE_SUBJECT
ACTION = email_mod.SUP_002_ACTION_SUBJECT
REMINDER = email_mod.SUP_002_REMINDER_SUBJECT
ESCALATED = email_mod.SUP_003_ESCALATED_SUBJECT
OWNER = email_mod.SUP_003_OWNER_SUBJECT
INCIDENT = email_mod.SUP_003_INCIDENT_SUBJECT
RESOLVED = email_mod.SUP_004_RESOLVED_SUBJECT
CLOSED = email_mod.SUP_004_CLOSED_SUBJECT
REOPENED = email_mod.SUP_004_REOPENED_SUBJECT
FEEDBACK = email_mod.SUP_004_FEEDBACK_SUBJECT

SECRET_NOTE = "INTERNAL: customer is on the churn list, escalate to retention. Ref ABUSE-77"


class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


class Captured:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "payload": json or {}})
        if self.fail:
            import httpx
            raise httpx.ConnectError("simulated Resend outage")
        return _Resp()

    @property
    def subjects(self):
        return [c["payload"]["subject"] for c in self.calls]

    def of(self, subject):
        hits = [c["payload"] for c in self.calls if c["payload"]["subject"] == subject]
        assert hits, f"no message with subject {subject!r}; got {self.subjects}"
        return hits[-1]

    def to(self, subject):
        return sorted(c["payload"]["to"][0].lower()
                      for c in self.calls if c["payload"]["subject"] == subject)

    def count(self, subject):
        return sum(1 for s in self.subjects if s == subject)

    def blob(self):
        return "".join(c["payload"].get("text", "") + c["payload"].get("html", "")
                       for c in self.calls)


_LEAKS: list[str] = []


def _deny(url, headers=None, json=None, timeout=None):
    _LEAKS.append((json or {}).get("subject", "?"))
    raise RuntimeError("outbound email attempted outside a capture context")


email_mod.httpx.post = _deny


def _capture(fail=False):
    cap = Captured(fail=fail)
    return cap, patch.object(email_mod.httpx, "post", cap)


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


def _new_email(tag="sup"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


def _now():
    return datetime.now(timezone.utc)


class World:
    """Two organizations, so tenant isolation is actually testable."""

    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Sup Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata")
            other = Organization(name=f"Other Co {uuid.uuid4().hex[:6]}", status="active",
                                 timezone="UTC")
            db.add_all([org, other])
            db.flush()
            self.org_id, self.other_org_id = org.id, other.id
            self.admin_email = _new_email("admin")
            self.admin_id = self._u(db, self.org_id, "org_admin", self.admin_email, "Org Admin")
            self.member_email = _new_email("member")
            self.member_id = self._u(db, self.org_id, "viewer", self.member_email, "Member")
            self.colleague_email = _new_email("colleague")
            self.outsider_email = _new_email("outsider")
            self.outsider_id = self._u(db, self.other_org_id, "org_admin",
                                       self.outsider_email, "Outsider")
            org.owner_user_id = self.admin_id
            other.owner_user_id = self.outsider_id
            db.commit()
        finally:
            db.close()

    def _u(self, db, org_id, role, email, name):
        user = User(org_id=org_id, full_name=name, role=role, is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=_now())
        db.add(user)
        db.flush()
        return user.id

    def token(self, email):
        client = TestClient(m.app)
        ratelimit._HITS.clear()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/auth/login",
                            json={"identifier": email, "password": PASSWORD},
                            headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def case(self, *, sensitivity="standard", category="technical", org_id=None):
        db = SessionLocal()
        try:
            org = db.get(Organization, org_id or self.org_id)
            requester = db.get(User, self.admin_id if (org_id or self.org_id) == self.org_id
                               else self.outsider_id)
            ticket = sc.open_case(db, org=org, requester=requester,
                                  subject="Stream keeps dropping",
                                  description="Our stream drops every few minutes.",
                                  category=category, priority="high",
                                  sensitivity=sensitivity)
            assert ticket is not None
            return ticket.id
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            for org_id in (self.org_id, self.other_org_id):
                for t in db.query(SupportTicket).filter(
                        SupportTicket.org_id == org_id).all():
                    db.query(SupportNotice).filter(
                        SupportNotice.ticket_id == t.id).delete()
                    db.query(SupportEscalation).filter(
                        SupportEscalation.ticket_id == t.id).delete()
                    db.query(SupportCaseParticipant).filter(
                        SupportCaseParticipant.ticket_id == t.id).delete()
                db.commit()
                db.query(SupportTicket).filter(SupportTicket.org_id == org_id).delete()
            db.commit()
            for org_id in (self.org_id, self.other_org_id):
                org = db.get(Organization, org_id)
                if org is not None:
                    org.owner_user_id = None
            db.commit()
            for org_id in (self.org_id, self.other_org_id):
                db.query(User).filter(User.org_id == org_id).delete()
                db.query(Organization).filter(Organization.id == org_id).delete()
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


RESULTS = []


def run(fn):
    world = World()
    try:
        fn(world)
        RESULTS.append((fn.__name__, None))
        print(f"ok  {fn.__name__}")
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((fn.__name__, exc))
        print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    finally:
        world.cleanup()


# ══ SUP-001 ═════════════════════════════════════════════════════════════════════════════

def test_authorized_customer_can_open_a_case(w):
    """1, 4, 5, 6, 7 - the core authorization fix, through the real HTTP surface."""
    client = TestClient(m.app)
    headers = w.token(w.admin_email)
    ratelimit._HITS.clear()
    cap, ctx = _capture()
    with ctx:
        r = client.post("/api/organization/support-cases",
                        json={"subject": "Cannot start my event",
                              "description": "The go-live button does nothing.",
                              "category": "live_event", "priority": "high"},
                        headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    # 4 - a real customer-facing reference, not a UUID.
    assert body["case_reference"].startswith("SUP-"), body["case_reference"]
    assert str(body["id"]) not in body["case_reference"]
    assert body["category"] == "live_event" and body["priority"] == "high"
    assert body["status"] == "open"

    # 7 - the case is committed before any mail goes out.
    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, uuid.UUID(body["id"]))
        assert ticket is not None and ticket.org_id == w.org_id
        assert ticket.requester_id == w.admin_id
    finally:
        db.close()
    # 8 - the requester is acknowledged.
    assert w.admin_email.lower() in cap.to(OPENED.format(case=body["case_reference"]))


def test_customer_cannot_reach_another_tenant(w):
    """2 - and the org is taken from the token, not the body."""
    other_case = w.case(org_id=w.other_org_id)
    client = TestClient(m.app)
    headers = w.token(w.admin_email)
    ratelimit._HITS.clear()
    r = client.get(f"/api/organization/support-cases/{other_case}", headers=headers)
    # 404 rather than 403: a customer must not be able to probe another tenant's references.
    assert r.status_code == 404, r.text

    ratelimit._HITS.clear()
    listed = client.get("/api/organization/support-cases", headers=headers)
    assert listed.status_code == 200
    assert all(c["id"] != str(other_case) for c in listed.json())

    # There is no org_id field to point elsewhere - the schema does not accept one.
    from app.schemas.organization import SupportCaseCreate
    assert "org_id" not in SupportCaseCreate.model_fields


def test_super_admin_routes_are_unchanged(w):
    """3 - Super Admin capability was not widened or weakened."""
    import inspect

    import app.routers.admin as admin_router

    source = inspect.getsource(admin_router)
    assert 'dependencies=[Depends(require_super_admin)]' in source, (
        "the admin router must keep its router-level guard")
    # The original create route still requires super admin.
    sig = inspect.getsource(admin_router.create_support_ticket)
    assert "require_super_admin" in sig
    # And an ordinary customer is refused there.
    client = TestClient(m.app)
    headers = w.token(w.admin_email)
    ratelimit._HITS.clear()
    r = client.post("/api/admin/support-tickets",
                    json={"org_id": str(w.org_id), "subject": "x", "message": "y"},
                    headers=headers)
    assert r.status_code in (401, 403), r.status_code


def test_only_case_participants_are_mailed(w):
    """9, 10 - case-scoped, never every organization member."""
    tid = w.case()
    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, tid)
        sc.add_participant(db, ticket, email=w.colleague_email,
                           display_name="Colleague")
        cap, ctx = _capture()
        with ctx:
            assert sc.notify_case_opened(db, _Bg(), ticket) is True
        got = cap.to(OPENED.format(case=ticket.case_reference))
        assert w.admin_email.lower() in got
        assert w.colleague_email.lower() in got
        # An ordinary member who is NOT on the case must not be mailed.
        assert w.member_email.lower() not in got, f"unrelated member mailed: {got}"
        assert w.outsider_email.lower() not in got
    finally:
        db.close()


def test_case_cta_never_reaches_super_admin(w):
    """11, 12, 13, 14, 15."""
    tid = w.case()
    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, tid)
        cap, ctx = _capture()
        with ctx:
            assert sc.notify_case_opened(db, _Bg(), ticket) is True
            n = len(cap.calls)
            assert sc.notify_case_opened(db, _Bg(), ticket) is False   # 14 dedup
        assert len(cap.calls) == n
        payload = cap.of(OPENED.format(case=ticket.case_reference))
        assert payload["html"].strip() and payload["text"].strip()   # 13
        blob = cap.blob()
        assert f"/organization/support/{ticket.id}" in blob           # 11
        for bad in ("/admin", "super_admin", "/api/admin"):
            assert bad not in blob, f"{bad} must not appear"          # 12
    finally:
        db.close()

    # 15 - a provider outage must not remove the case.
    tid2 = w.case()
    db = SessionLocal()
    try:
        ticket2 = db.get(SupportTicket, tid2)
        capf, ctxf = _capture(fail=True)
        with ctxf:
            sc.notify_case_opened(db, _Bg(), ticket2)
        db.expire_all()
        assert db.get(SupportTicket, tid2) is not None
    finally:
        db.close()


# ══ SUP-002 ═════════════════════════════════════════════════════════════════════════════

def test_internal_notes_never_reach_the_customer(w):
    """2, 9 - an internal edit is silent, and notes have no route into a template."""
    import inspect

    tid = w.case()
    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, tid)
        ticket.internal_notes = SECRET_NOTE
        db.commit()

        # 2 - an internal-only change is not a customer-visible update.
        assert sc.is_customer_visible({"internal_notes": SECRET_NOTE}) is False
        assert sc.is_customer_visible({"tags": ["x"]}) is False
        assert sc.is_customer_visible({"sensitivity": "security"}) is False
        assert sc.is_customer_visible({"customer_update": "hello"}) is True
        assert sc.is_customer_visible({"status": "resolved"}) is True

        sc.post_update(db, ticket, customer_update="We reproduced the drop and are fixing it.")
        cap, ctx = _capture()
        with ctx:
            assert sc.notify_case_update(db, _Bg(), ticket) is True
        blob = cap.blob()
        assert "We reproduced the drop" in blob
        for leak in ("INTERNAL", "churn list", "ABUSE-77", "retention"):
            assert leak not in blob, f"internal note leaked: {leak}"

        # Structural: no sender accepts internal_notes at all.
        for name in [n for n in dir(email_mod) if n.startswith("send_support_")]:
            params = inspect.signature(getattr(email_mod, name)).parameters
            assert "internal_notes" not in params, name
    finally:
        db.close()


def test_action_required_and_reminder(w):
    """1, 3, 4, 5, 6, 7 - a waiting case must not stall silently."""
    tid = w.case()
    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, tid)
        assert sc.request_customer_action(
            db, ticket, action="Send the encoder log from the failed session") is True
        assert ticket.status == "waiting_for_customer"       # 3 authoritative
        assert ticket.action_version == 1

        cap, ctx = _capture()
        with ctx:
            assert sc.notify_action_required(db, _Bg(), ticket) is True
        text = cap.of(ACTION.format(case=ticket.case_reference))["text"]
        assert "Send the encoder log" in text                # 5
        assert "rather than by email" in text                # sensitive data stays in portal

        # 6 - no reminder yet: the threshold has not passed.
        due, why = sc.reminder_due(ticket)
        assert due is False, why
        ticket.updated_at = _now() - timedelta(hours=sc.ACTION_REMINDER_HOURS + 1)
        db.commit()
        due, why = sc.reminder_due(ticket)
        assert due is True, why
        cap2, ctx2 = _capture()
        with ctx2:
            assert sc.notify_action_reminder(db, _Bg(), ticket) is True
            n = len(cap2.calls)
            assert sc.notify_action_reminder(db, _Bg(), ticket) is False
        assert len(cap2.calls) == n
        assert cap2.count(REMINDER.format(case=ticket.case_reference)) >= 1

        # 7 - completing the action stops reminding.
        assert sc.complete_customer_action(db, ticket) is True
        due, why = sc.reminder_due(ticket)
        assert due is False and "in_progress" in why
    finally:
        db.close()


def test_multiple_genuine_updates_each_send(w):
    """1, 10 - several real updates are each single-shot, not collapsed into one."""
    tid = w.case()
    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, tid)
        cap, ctx = _capture()
        with ctx:
            sc.post_update(db, ticket, customer_update="First update.")
            assert sc.notify_case_update(db, _Bg(), ticket) is True
            sc.post_update(db, ticket, customer_update="Second update.")
            assert sc.notify_case_update(db, _Bg(), ticket) is True
        assert cap.count(UPDATE.format(case=ticket.case_reference)) >= 2
        payload = cap.of(UPDATE.format(case=ticket.case_reference))
        assert payload["html"].strip() and payload["text"].strip()
        assert "Second update." in payload["text"]
    finally:
        db.close()


# ══ SUP-003 ═════════════════════════════════════════════════════════════════════════════

def test_escalation_uses_real_state(w):
    """1, 2, 3, 11, 12."""
    tid = w.case()
    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, tid)
        assert sc.escalate(db, ticket, level=2, reason_category="not_a_reason") is None
        escalation = sc.escalate(db, ticket, level=2, reason_category="impact",
                                 owner_after="Zoiko Steam Live Operations")
        assert escalation is not None and escalation.escalated_at is not None
        assert escalation.owner_before == "Zoiko Steam Support"

        cap, ctx = _capture()
        with ctx:
            assert sc.notify_escalated(db, _Bg(), ticket, escalation) is True
            n = len(cap.calls)
            assert sc.notify_escalated(db, _Bg(), ticket, escalation) is False  # 12
        assert len(cap.calls) == n
        text = cap.of(ESCALATED.format(case=ticket.case_reference))["text"]
        assert "The impact on your service" in text          # 3 safe category
        for leak in ("sev1", "sev2", "detection", "exploit", "commander"):
            assert leak not in text.lower(), f"internal detail leaked: {leak}"
    finally:
        db.close()


def test_next_update_is_never_invented(w):
    """9, 10 - the critical rule."""
    tid = w.case()
    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, tid)
        assert ticket.next_update_at is None
        note = sc.next_update_note(ticket)
        assert note == "We will update the case when new information is available."
        escalation = sc.escalate(db, ticket, level=1, reason_category="duration")
        cap, ctx = _capture()
        with ctx:
            sc.notify_escalated(db, _Bg(), ticket, escalation)
        text = cap.of(ESCALATED.format(case=ticket.case_reference))["text"]
        assert "when new information is available" in text
        for invented in ("within 2 hours", "within 24 hours", "shortly", "asap",
                         "we will update you by"):
            assert invented not in text.lower(), f"fabricated SLA: {invented}"

        # With a stored commitment, and only then, a time is quoted.
        promised = _now() + timedelta(hours=4)
        ticket.next_update_at = promised
        db.commit()
        assert "We will update you by" in sc.next_update_note(ticket)
        # And no template composes one itself.
        import inspect
        for name in [n for n in dir(email_mod) if n.startswith("send_support_")]:
            src = inspect.getsource(getattr(email_mod, name))
            assert "timedelta" not in src and "hours" not in src, name
    finally:
        db.close()


def test_owner_change_ignores_internal_reassignment(w):
    """4, 5, 6."""
    tid = w.case()
    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, tid)
        # 6 - same team: not a customer-facing change.
        changed, previous = sc.change_owner(db, ticket, owner_after="Zoiko Steam Support")
        assert changed is False and previous == "Zoiko Steam Support"
        cap, ctx = _capture()
        with ctx:
            assert sc.notify_owner_changed(db, _Bg(), ticket, previous=previous) is False
        assert cap.calls == []

        changed, previous = sc.change_owner(db, ticket,
                                            owner_after="Zoiko Steam Media Operations")
        assert changed is True
        assert ticket.assigned_owner == "Zoiko Steam Media Operations"
        cap2, ctx2 = _capture()
        with ctx2:
            assert sc.notify_owner_changed(db, _Bg(), ticket, previous=previous) is True
        text = cap2.of(OWNER.format(case=ticket.case_reference))["text"]
        assert "Zoiko Steam Support" in text and "Media Operations" in text
    finally:
        db.close()


def test_incident_link_uses_real_incident_and_hides_detail(w):
    """7, 8, 11 - reuses platform_ops.Incident, exposes only safe fields."""
    db = SessionLocal()
    try:
        incident = Incident(ref=f"INC-2027-{uuid.uuid4().hex[:6].upper()}",
                            title="Ingest degradation in eu-west",
                            detail="Root cause: encoder pool exhausted by CVE-2027-1234 patch rollback",
                            severity="sev1", kind="security", status="monitoring",
                            commander="Jane Ops")
        db.add(incident)
        db.commit()
        tid = w.case()
        ticket = db.get(SupportTicket, tid)
        assert sc.link_incident(db, ticket, incident) is True
        assert ticket.incident_id == incident.id

        cap, ctx = _capture()
        with ctx:
            assert sc.notify_incident_linked(db, _Bg(), ticket) is True
        blob = cap.blob()
        assert incident.ref in blob                            # approved reference
        assert "Fix applied, being monitored" in blob          # safe status
        for leak in ("Root cause", "CVE-2027-1234", "encoder pool", "Jane Ops", "sev1",
                     "eu-west"):
            assert leak not in blob, f"incident detail leaked: {leak}"
        db.delete(incident)
        db.commit()
    finally:
        db.close()


# ══ SUP-004 ═════════════════════════════════════════════════════════════════════════════

def test_resolve_close_and_reopen_are_distinct(w):
    """1, 2, 3, 4, 5, 6 - and a reopen permits a SECOND resolution notice."""
    tid = w.case()
    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, tid)
        assert sc.resolve(db, ticket, summary="Replaced the ingest endpoint.") is True
        assert ticket.status == "resolved" and ticket.resolved_at is not None
        cap, ctx = _capture()
        with ctx:
            assert sc.notify_resolved(db, _Bg(), ticket) is True
            assert sc.notify_resolved(db, _Bg(), ticket) is False
        assert cap.count(RESOLVED.format(case=ticket.case_reference)) == 1

        # 3, 4 - closed is a distinct state with its own notice.
        assert sc.close(db, ticket) is True
        assert ticket.status == "closed" and ticket.closed_at is not None
        cap2, ctx2 = _capture()
        with ctx2:
            assert sc.notify_closed(db, _Bg(), ticket) is True
        assert cap2.count(CLOSED.format(case=ticket.case_reference)) == 1

        # 5, 6 - reopen starts a new cycle.
        before = ticket.lifecycle_cycle
        assert sc.reopen(db, ticket, reason="It happened again.") is True
        assert ticket.status == "reopened" and ticket.lifecycle_cycle == before + 1
        cap3, ctx3 = _capture()
        with ctx3:
            assert sc.notify_reopened(db, _Bg(), ticket) is True
        assert cap3.count(REOPENED.format(case=ticket.case_reference)) == 1

        # The whole point of cycle-keyed idempotency: a SECOND resolution is legitimate.
        assert sc.resolve(db, ticket, summary="Fixed properly this time.") is True
        cap4, ctx4 = _capture()
        with ctx4:
            assert sc.notify_resolved(db, _Bg(), ticket) is True, (
                "a resolution after a reopen must be announceable again")
        assert "Fixed properly this time." in cap4.of(
            RESOLVED.format(case=ticket.case_reference))["text"]
    finally:
        db.close()


def test_feedback_for_an_eligible_case(w):
    """7, 8, 13, 14."""
    tid = w.case(sensitivity="standard", category="technical")
    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, tid)
        eligible, why = sc.is_feedback_eligible(ticket)
        assert eligible is False and "not resolved" in why
        sc.resolve(db, ticket, summary="Done.")
        eligible, why = sc.is_feedback_eligible(ticket)
        assert eligible is True, why
        cap, ctx = _capture()
        with ctx:
            assert sc.notify_feedback_request(db, _Bg(), ticket) is True
            assert sc.notify_feedback_request(db, _Bg(), ticket) is False   # 13
        assert cap.count(FEEDBACK) == 1
        payload = cap.of(FEEDBACK)
        assert payload["html"].strip() and payload["text"].strip()          # 14
    finally:
        db.close()


def test_feedback_excluded_for_sensitive_cases(w):
    """9, 10, 11 - decided from classification, never from wording."""
    for sensitivity in FEEDBACK_EXCLUDED_SENSITIVITIES:
        tid = w.case(sensitivity=sensitivity)
        db = SessionLocal()
        try:
            ticket = db.get(SupportTicket, tid)
            # Eligibility was refused at creation, not merely at send time.
            assert ticket.feedback_eligible is False, sensitivity
            sc.resolve(db, ticket, summary="Handled.")
            eligible, why = sc.is_feedback_eligible(ticket)
            assert eligible is False, f"{sensitivity} must not be surveyed: {why}"
            cap, ctx = _capture()
            with ctx:
                assert sc.notify_feedback_request(db, _Bg(), ticket) is False
                # The resolution itself still goes out - only the survey is suppressed.
                assert sc.notify_resolved(db, _Bg(), ticket) is True
            assert cap.count(FEEDBACK) == 0, f"{sensitivity} received a survey"
        finally:
            db.close()


def test_sensitivity_is_not_inferred_from_wording(w):
    """9 - the authoritative classification wins over alarming text."""
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        requester = db.get(User, w.admin_id)
        # Alarming wording, but classified standard: it IS surveyed.
        loud = sc.open_case(db, org=org, requester=requester,
                            subject="URGENT my account was hacked and breached",
                            description="security incident compromise fraud",
                            category="billing", sensitivity="standard")
        sc.resolve(db, loud, summary="It was a duplicate charge.")
        eligible, _why = sc.is_feedback_eligible(loud)
        assert eligible is True, (
            "sensitivity must come from classification, not keyword matching")

        # Neutral wording, classified bereavement: it is NOT surveyed.
        quiet = sc.open_case(db, org=org, requester=requester,
                             subject="Please help with an event",
                             description="We need to change some details.",
                             category="other", sensitivity="bereavement")
        sc.resolve(db, quiet, summary="Handled with care.")
        eligible, why = sc.is_feedback_eligible(quiet)
        assert eligible is False, why
    finally:
        db.close()


def test_feedback_creates_no_marketing_consent(w):
    """12 - support activity is operational processing, not marketing consent."""
    from app.db import Base
    from app.services import notifications
    import inspect

    tid = w.case()
    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, tid)
        sc.resolve(db, ticket, summary="Done.")
        cap, ctx = _capture()
        with ctx:
            assert sc.notify_feedback_request(db, _Bg(), ticket) is True
        text = cap.of(FEEDBACK)["text"]
        assert "does not subscribe you to marketing" in text
        assert notifications.MARKETING_PREFERENCE_KEYS == frozenset()
        for table in Base.metadata.tables:
            for word in ("newsletter", "mailing_list", "audience_list"):
                assert word not in table.lower(), f"a marketing table appeared: {table}"
        # The product has two subscription lists and a support requester must be on neither:
        # `status_subscribers` (verified opt-in, operational) and `marketing_subscriptions`
        # (ZST-EC-001 MKT - the only marketing consent record there is). Asserting the
        # requester's ADDRESS is absent from both is stronger than the old table-name scan.
        from app.models import MARKETING_TOPICS, MarketingSubscription, StatusSubscriber
        from app.services import marketing as mkt

        assert db.query(StatusSubscriber).filter(
            StatusSubscriber.email == ticket.requester_email).count() == 0, (
            "a feedback request must not create a status subscription")
        assert db.query(MarketingSubscription).filter(
            MarketingSubscription.email == ticket.requester_email).count() == 0, (
            "a feedback request must not create marketing consent")
        for topic in MARKETING_TOPICS:
            assert mkt.eligible(db, topic, ticket.requester_email) is False, topic
        # And the service can reach no marketing or audience sender.
        called = {n.func.attr for n in __import__("ast").walk(
            __import__("ast").parse(inspect.getsource(sc)))
            if isinstance(n, __import__("ast").Call)
            and isinstance(n.func, __import__("ast").Attribute)}
        for sender in ("subscribe", "add_subscriber", "send_viewer_invite_email"):
            assert sender not in called, f"support_comms calls {sender}"
    finally:
        db.close()


# ══ cross-cutting ═══════════════════════════════════════════════════════════════════════

def test_families_classified_and_no_secret_parameters(w):
    from app.services import notifications
    import inspect

    for family in ("SUP-001", "SUP-002", "SUP-003", "SUP-004"):
        assert family in notifications.FAMILY_CLASS, family
    for mandatory in ("SUP-001", "SUP-002", "SUP-004"):
        assert notifications.is_mandatory(mandatory) is True, mandatory

    senders = [n for n in dir(email_mod)
               if n.startswith("send_support_") and "access" not in n]
    assert len(senders) >= 10, senders
    banned = ("internal_notes", "password", "token", "secret", "api_key", "commander",
               "severity", "detail", "logs")
    for name in senders:
        for param in inspect.signature(getattr(email_mod, name)).parameters:
            assert not any(b == param.lower() for b in banned), f"{name}({param})"


def test_idempotency_key_is_not_permanently_coarse(w):
    """The spec's explicit warning: UNIQUE(kind, case) would block a valid re-resolution."""
    cols = {c.name for c in SupportNotice.__table__.columns}
    assert {"cycle", "sequence"} <= cols, (
        "the notice key must include a cycle so RESOLVED -> REOPENED -> RESOLVED works")
    constraint = next(c for c in SupportNotice.__table__.constraints
                      if c.__class__.__name__ == "UniqueConstraint")
    names = {c.name for c in constraint.columns}
    assert names == {"kind", "ticket_id", "cycle", "sequence"}, names


def test_reminder_sweep_runs_on_the_shared_ticker(w):
    """No SUP-specific queue when shared infrastructure exists."""
    import inspect

    from app.services import event_planning

    assert "support_comms" in inspect.getsource(event_planning.sweep)
    assert not hasattr(sc, "run_support_sweeper"), (
        "SUP must not add its own ticker alongside the leader-elected one")


TESTS = [
    test_authorized_customer_can_open_a_case,
    test_customer_cannot_reach_another_tenant,
    test_super_admin_routes_are_unchanged,
    test_only_case_participants_are_mailed,
    test_case_cta_never_reaches_super_admin,
    test_internal_notes_never_reach_the_customer,
    test_action_required_and_reminder,
    test_multiple_genuine_updates_each_send,
    test_escalation_uses_real_state,
    test_next_update_is_never_invented,
    test_owner_change_ignores_internal_reassignment,
    test_incident_link_uses_real_incident_and_hides_detail,
    test_resolve_close_and_reopen_are_distinct,
    test_feedback_for_an_eligible_case,
    test_feedback_excluded_for_sensitive_cases,
    test_sensitivity_is_not_inferred_from_wording,
    test_feedback_creates_no_marketing_consent,
    test_families_classified_and_no_secret_parameters,
    test_idempotency_key_is_not_permanently_coarse,
    test_reminder_sweep_runs_on_the_shared_ticker,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
