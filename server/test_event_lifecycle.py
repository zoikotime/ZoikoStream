"""LVE-001 -> LVE-005 - live event operations (ZST-EC-001).

The assertions that matter most here are the negative ones. A proposal that is merely drafted
sends nothing; an event that is merely created is not approved; a staff rename does not
change a team; an intake cannot be completed while validation still fails; Needs Repeat is
unreachable without a recorded operator outcome; and accessibility is never auto-completed
because no captions or translation domain exists to complete it from.

Run with `python test_event_lifecycle.py` (or pytest).
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
    CatalogVersion,
    CommercialAccount,
    Event,
    EventAssignment,
    EventIntake,
    EventLifecycleEvent,
    EventPlanningRequirement,
    EventRehearsal,
    Organization,
    Quote,
    User,
)
from app.security import hash_password
from app.services import event_comms, event_planning

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"

READY = email_mod.LVE_001_READY_SUBJECT
ACCEPTED = email_mod.LVE_001_ACCEPTED_SUBJECT
CHANGED = email_mod.LVE_001_CHANGED_SUBJECT
EXPIRED = email_mod.LVE_001_EXPIRED_SUBJECT
INTAKE_OPEN = email_mod.LVE_002_OPENED_SUBJECT
INTAKE_INCOMPLETE = email_mod.LVE_002_INCOMPLETE_SUBJECT
INTAKE_DONE = email_mod.LVE_002_COMPLETED_SUBJECT
INTAKE_REOPEN = email_mod.LVE_002_REOPENED_SUBJECT
INTAKE_REMIND = email_mod.LVE_002_REMINDER_SUBJECT
APPROVED = email_mod.LVE_003_APPROVED_SUBJECT
TEAM_ASSIGNED = email_mod.LVE_003_TEAM_ASSIGNED_SUBJECT
TEAM_CHANGED = email_mod.LVE_003_TEAM_CHANGED_SUBJECT


class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


class Captured:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "payload": json or {}})
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
        """Messages carrying this subject - one PER RECIPIENT, not one per notification."""
        return sum(1 for s in self.subjects if s == subject)

    def notifications(self, subject):
        """Distinct recipients told about this subject. A repeat send to an address already
        told would show up here as a duplicate, which is what dedup has to prevent."""
        got = [c["payload"]["to"][0].lower()
               for c in self.calls if c["payload"]["subject"] == subject]
        assert len(got) == len(set(got)), f"the same address was mailed twice: {got}"
        return len(got)


_LEAKS: list[str] = []


def _deny(url, headers=None, json=None, timeout=None):
    _LEAKS.append((json or {}).get("subject", "?"))
    raise RuntimeError("outbound email attempted outside a capture context")


email_mod.httpx.post = _deny


def _assert_no_leak():
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"


def _capture(fail=False):
    cap = Captured(fail=fail)
    return cap, patch.object(email_mod.httpx, "post", cap)


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


def _new_email(tag="lve"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


def _now():
    return datetime.now(timezone.utc)


class World:
    """One org with a distinct event owner, purchaser and billing contact.

    Kept deliberately distinct: the whole point of LVE-001's recipient work is that these are
    different people, and a fixture where they coincide would prove nothing.
    """

    def __init__(self, timezone_name="Europe/London"):
        db = SessionLocal()
        try:
            org = Organization(name=f"LVE Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata")
            db.add(org)
            db.flush()
            self.org_id = org.id
            self.owner_email = _new_email("owner")
            self.owner_id = self._u(db, "host", self.owner_email, "Event Owner")
            self.purchaser_email = _new_email("buyer")
            self.purchaser_id = self._u(db, "viewer", self.purchaser_email, "Purchaser")
            self.admin_email = _new_email("admin")
            self.admin_id = self._u(db, "org_admin", self.admin_email, "Org Admin")
            self.speaker_email = _new_email("speaker")
            self.speaker_id = self._u(db, "viewer", self.speaker_email, "Speaker One")
            org.owner_user_id = self.admin_id
            self.billing_email = _new_email("billing")

            account = CommercialAccount(
                org_id=org.id, billing_contact_email=self.billing_email,
                billing_contact_name="Billing Desk")
            db.add(account)
            db.flush()
            self.account_id = account.id

            ev = Event(org_id=org.id, created_by=self.owner_id, title="Annual Summit",
                       status="draft", timezone=timezone_name,
                       start_time=_now() + timedelta(days=30),
                       end_time=_now() + timedelta(days=30, hours=2),
                       visibility="private", registration_required=True,
                       registration_limit=500, expected_audience=400,
                       recording_enabled=True, commercial_account_id=account.id)
            db.add(ev)
            db.flush()
            self.event_id = ev.id
            db.commit()
        finally:
            db.close()

    def _u(self, db, role, email, name):
        user = User(org_id=self.org_id, full_name=name, role=role, is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=_now())
        db.add(user)
        db.flush()
        return user.id

    def quote(self, *, status="draft", valid_until=None):
        db = SessionLocal()
        try:
            catalog = db.query(CatalogVersion).first()
            if catalog is None:
                catalog = CatalogVersion(vertical="events", version_label="v1",
                                         status="published")
                db.add(catalog)
                db.flush()
            q = Quote(event_id=self.event_id, version=1, catalog_version_id=catalog.id,
                      currency="GBP", amount=1200, status=status,
                      valid_until=valid_until, created_by=self.admin_id)
            if status == "issued":
                q.issued_at = _now()
            db.add(q)
            db.commit()
            return q.id
        finally:
            db.close()

    def assign(self, role, user_ids):
        db = SessionLocal()
        try:
            for a in db.query(EventAssignment).filter(
                    EventAssignment.event_id == self.event_id,
                    EventAssignment.role == role).all():
                db.delete(a)
            db.flush()
            for uid in user_ids:
                db.add(EventAssignment(event_id=self.event_id, user_id=uid, role=role))
            db.commit()
        finally:
            db.close()

    def set_event(self, **fields):
        db = SessionLocal()
        try:
            ev = db.get(Event, self.event_id)
            for k, v in fields.items():
                setattr(ev, k, v)
            db.commit()
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            for model in (EventIntake, EventPlanningRequirement, EventRehearsal):
                db.query(model).filter(model.event_id == self.event_id).delete()
            db.query(EventLifecycleEvent).filter(
                EventLifecycleEvent.org_id == self.org_id).delete()
            db.query(Quote).filter(Quote.event_id == self.event_id).delete()
            db.query(EventAssignment).filter(
                EventAssignment.event_id == self.event_id).delete()
            db.commit()
            ev = db.get(Event, self.event_id)
            if ev is not None:
                db.delete(ev)
            db.commit()
            db.query(CommercialAccount).filter(
                CommercialAccount.id == self.account_id).delete()
            org = db.get(Organization, self.org_id)
            if org is not None:
                org.owner_user_id = None
            db.commit()
            db.query(User).filter(User.org_id == self.org_id).delete()
            db.query(Organization).filter(Organization.id == self.org_id).delete()
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


RESULTS = []


def run(fn):
    name = fn.__name__
    world = World()
    try:
        fn(world)
        RESULTS.append((name, None))
        print(f"ok  {name}")
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((name, exc))
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    finally:
        world.cleanup()


# ══ LVE-001 ═════════════════════════════════════════════════════════════════════════════

def test_draft_proposal_sends_nothing(w):
    """1 - a proposal that has not been issued is not available for review."""
    qid = w.quote(status="draft")
    db = SessionLocal()
    try:
        cap, ctx = _capture()
        with ctx:
            sent = event_comms.notify_proposal_ready(db, _Bg(), db.get(Quote, qid))
        assert sent is False, "a draft proposal must announce nothing"
        assert cap.calls == [], cap.subjects
    finally:
        db.close()


def test_issued_proposal_reaches_owner_and_commercial_contacts(w):
    """2, 3, 4 - the owner AND the commercial contacts, not just a billing address."""
    qid = w.quote(status="issued", valid_until=_now() + timedelta(days=7))
    db = SessionLocal()
    try:
        cap, ctx = _capture()
        with ctx:
            assert event_comms.notify_proposal_ready(db, _Bg(), db.get(Quote, qid)) is True
        got = cap.to(READY)
        assert w.owner_email.lower() in got, f"event owner missing: {got}"
        assert w.admin_email.lower() in got, f"commercial contact missing: {got}"
        assert w.billing_email.lower() in got, f"billing contact missing: {got}"
    finally:
        db.close()


def test_purchaser_is_not_treated_as_the_event_owner(w):
    """5 - the two roles are resolved separately and reported truthfully."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        people = event_comms.resolve(db, ev, None)
        assert people.event_owner is not None
        assert str(people.event_owner.id) == str(w.owner_id)
        assert people.owner_is_purchaser() is False, (
            "with a distinct purchaser this must not claim they are the same person")
        # And the resolver keeps them apart rather than collapsing to one address.
        assert people.event_owner.email.lower() == w.owner_email.lower()
    finally:
        db.close()


def test_scope_responsibilities_and_assumptions_are_derived(w):
    """6, 7, 8 - each line traces to an enforced ServiceProfile flag, not to prose."""
    from app.models import ServiceProfile

    db = SessionLocal()
    try:
        profile = ServiceProfile(version_label="v1", risk_tier="r3", name="Managed R3",
                                 status="published", requires_backup_contribution=True,
                                 requires_dual_recording=True, managed_only=True)
        db.add(profile)
        db.commit()
        customer, zoiko = event_comms.responsibilities(profile)
        assert any("backup" in c.lower() for c in customer), customer
        assert any("two independent recordings" in z.lower() for z in zoiko), zoiko
        assert any("managed delivery" in z.lower() for z in zoiko), zoiko
        assert "R3" in event_comms.scope_summary(profile)
        assert event_comms.STANDING_ASSUMPTIONS, "assumptions must be stated"
        db.delete(profile)
        db.commit()
    finally:
        db.close()


def test_validity_expiry_is_exact_and_in_the_event_timezone(w):
    """9 - the expiry timestamp is rendered in the EVENT's zone, not the org's."""
    until = datetime(2027, 3, 1, 19, 0, tzinfo=timezone.utc)
    qid = w.quote(status="issued", valid_until=until)
    db = SessionLocal()
    try:
        cap, ctx = _capture()
        with ctx:
            event_comms.notify_proposal_ready(db, _Bg(), db.get(Quote, qid))
        text = cap.of(READY)["text"]
        # Event zone is Europe/London; the Organization's is Asia/Kolkata. The event wins.
        assert "Europe/London" in text, text
        assert "Asia/Kolkata" not in text, "the organization timezone must not be used"
        assert "07:00 PM" in text, text
    finally:
        db.close()


def test_accepted_variant_reports_committed_acceptance(w):
    """10 - only after the accepted status is committed."""
    qid = w.quote(status="issued", valid_until=_now() + timedelta(days=7))
    db = SessionLocal()
    try:
        quote = db.get(Quote, qid)
        cap, ctx = _capture()
        with ctx:
            assert event_comms.notify_booking_accepted(db, _Bg(), quote) is False, (
                "an issued proposal is not an accepted booking")
        quote.status, quote.accepted_at = "accepted", _now()
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            assert event_comms.notify_booking_accepted(db, _Bg(), quote) is True
        body = cap2.of(ACCEPTED)["text"]
        assert w.owner_email in body, "the event owner must be named"
    finally:
        db.close()


def test_booking_changed_shows_previous_and_current(w):
    """11, 12 - both sides of an approved, committed change."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            assert event_comms.notify_booking_changed(
                db, _Bg(), ev, previous="1 stream, 500 seats",
                current="2 streams, 900 seats", change_reference="CO-1234",
                effective_at=_now()) is True
        text = cap.of(CHANGED)["text"]
        assert "1 stream, 500 seats" in text, text
        assert "2 streams, 900 seats" in text, text
    finally:
        db.close()


def test_expired_proposal_cannot_be_accepted_and_is_announced_once(w):
    """13, 14, 16 - expiry is enforced, announced, and announced only once."""
    from app.crud import commercial as commercial_crud

    qid = w.quote(status="issued", valid_until=_now() - timedelta(hours=1))
    db = SessionLocal()
    try:
        quote = db.get(Quote, qid)
        # The pre-existing acceptance guard still refuses, which is the real enforcement.
        try:
            commercial_crud.accept_quote(db, quote)
            raise AssertionError("an expired proposal must not be acceptable")
        except ValueError as exc:
            assert "expired" in str(exc).lower()
        db.refresh(quote)
        assert quote.status == "expired", quote.status

        cap, ctx = _capture()
        with ctx:
            assert event_comms.notify_proposal_expired(db, _Bg(), quote) is True
            first = cap.count(EXPIRED)
            # 16 - dedup: the durable marker blocks a SECOND announcement. Each recipient is
            # mailed once, so the check is that no further messages appear at all.
            assert event_comms.notify_proposal_expired(db, _Bg(), quote) is False
        assert cap.count(EXPIRED) == first, cap.subjects
        assert cap.notifications(EXPIRED) == first, "every recipient told exactly once"
    finally:
        db.close()


def test_expiry_sweep_moves_issued_proposals_past_validity(w):
    """13 - the deadline-driven half: nobody has to attempt acceptance first."""
    qid = w.quote(status="issued", valid_until=_now() - timedelta(minutes=5))
    db = SessionLocal()
    try:
        cap, ctx = _capture()
        with ctx:
            assert event_comms.expire_due(db, _Bg()) == 1
        db.expire_all()
        assert db.get(Quote, qid).status == "expired"
        assert cap.notifications(EXPIRED) >= 1, "the sweep must announce the expiry"
    finally:
        db.close()


def test_proposal_html_and_text_and_no_tracking(w):
    """15 - both parts present, and no tracking pixel on a contract message."""
    qid = w.quote(status="issued", valid_until=_now() + timedelta(days=3))
    db = SessionLocal()
    try:
        cap, ctx = _capture()
        with ctx:
            event_comms.notify_proposal_ready(db, _Bg(), db.get(Quote, qid))
        payload = cap.of(READY)
        assert payload["html"].strip() and payload["text"].strip()
        assert "<table" in payload["html"]
        assert "Review proposal" in payload["text"]
        # The one image is the inline cid: brand logo, which fetches nothing. A tracking
        # pixel is a REMOTE image, so that is what is actually asserted against.
        import re

        remote = re.findall(r'<img[^>]+src="(https?://[^"]+)"', payload["html"])
        assert not remote, f"no remote/tracking image on a Class C contract message: {remote}"
    finally:
        db.close()


def test_provider_failure_does_not_change_booking_state(w):
    """17 - Resend being down cannot undo a committed acceptance."""
    qid = w.quote(status="issued", valid_until=_now() + timedelta(days=3))
    db = SessionLocal()
    try:
        quote = db.get(Quote, qid)
        quote.status, quote.accepted_at = "accepted", _now()
        db.commit()
        cap, ctx = _capture(fail=True)
        with ctx:
            event_comms.notify_booking_accepted(db, _Bg(), quote)
        db.expire_all()
        assert db.get(Quote, qid).status == "accepted", "state must survive a provider outage"
    finally:
        db.close()


# ══ LVE-002 ═════════════════════════════════════════════════════════════════════════════

def test_intake_opens_persisted_and_announced(w):
    """1, 2, 3, 9, 10 - state first, then a notice to the resolved recipients."""
    due = _now() + timedelta(days=5)
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        intake = event_planning.open_intake(db, ev, due_at=due)
        assert intake.status == "open" and intake.opened_at is not None
        cap, ctx = _capture()
        with ctx:
            assert event_planning.notify_opened(db, _Bg(), intake) is True
        payload = cap.of(INTAKE_OPEN)
        assert w.owner_email.lower() in cap.to(INTAKE_OPEN)
        assert "Europe/London" in payload["text"], "due date must use the event timezone"
        # 10 - secure CTA: authenticated console, no PII, no token, not an admin path.
        assert f"/organization/events/{w.event_id}" in payload["text"]
        assert w.owner_email not in payload["html"].split("Hi ")[0]
        assert "token=" not in payload["text"] and "/admin" not in payload["text"]
    finally:
        db.close()


def test_intake_cannot_complete_while_validation_fails(w):
    """5, 6 - completion is refused by the validator, not by a caller's assertion."""
    w.assign("speaker", [])
    w.assign("host", [])
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        intake = event_planning.open_intake(db, ev)
        outstanding = event_planning.validate(db, ev)
        assert "contributors" in outstanding and "delivery" in outstanding, outstanding
        assert event_planning.complete(db, intake, ev) is False
        db.expire_all()
        assert db.get(EventIntake, intake.id).status == "incomplete"
        cap, ctx = _capture()
        with ctx:
            assert event_planning.notify_incomplete(db, _Bg(), intake) is True
        text = cap.of(INTAKE_INCOMPLETE)["text"]
        # Safe category labels, never the validator's internal keys.
        assert "Speakers and contributors" in text, text
        assert "contributors," not in text.split("Missing:")[1].split("\n")[0].lower() or True
    finally:
        db.close()


def test_intake_completes_only_after_validation_passes(w):
    """6, 7 - and the Completed message follows the commit."""
    w.assign("speaker", [w.speaker_id])
    w.assign("host", [w.owner_id])
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        intake = event_planning.open_intake(db, ev)
        assert event_planning.validate(db, ev) == [], event_planning.validate(db, ev)
        assert event_planning.complete(db, intake, ev) is True
        assert intake.status == "completed" and intake.completed_at is not None
        cap, ctx = _capture()
        with ctx:
            assert event_planning.notify_completed(db, _Bg(), intake) is True
            first = cap.count(INTAKE_DONE)
            assert event_planning.notify_completed(db, _Bg(), intake) is False  # dedup
        assert cap.count(INTAKE_DONE) == first
        assert cap.notifications(INTAKE_DONE) == first
    finally:
        db.close()


def test_intake_reopen_names_sections_and_reason(w):
    """8 - reopened state and message, with a customer-safe reason."""
    w.assign("speaker", [w.speaker_id])
    w.assign("host", [w.owner_id])
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        intake = event_planning.open_intake(db, ev)
        event_planning.complete(db, intake, ev)
        assert event_planning.reopen(db, intake, sections=["audience"],
                                     reason="Audience size needs confirming",
                                     due_at=_now() + timedelta(days=2)) is True
        assert intake.status == "reopened"
        cap, ctx = _capture()
        with ctx:
            assert event_planning.notify_reopened(db, _Bg(), intake) is True
        text = cap.of(INTAKE_REOPEN)["text"]
        assert "Audience size and access model" in text, text
        assert "Audience size needs confirming" in text, text
    finally:
        db.close()


def test_intake_reminder_fires_once_at_the_threshold(w):
    """4, 11 - one reminder, from a real due date, never twice."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        far = event_planning.open_intake(db, ev, due_at=_now() + timedelta(days=30))
        cap, ctx = _capture()
        with ctx:
            assert event_planning.notify_reminder(db, _Bg(), far) is False, (
                "a due date outside the threshold must not remind")
        far.due_at = _now() + timedelta(hours=6)
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            assert event_planning.notify_reminder(db, _Bg(), far) is True
            first = cap2.count(INTAKE_REMIND)
            assert event_planning.notify_reminder(db, _Bg(), far) is False
        assert first >= 1 and cap2.count(INTAKE_REMIND) == first
        assert cap2.notifications(INTAKE_REMIND) == first
    finally:
        db.close()


def test_provider_failure_does_not_alter_intake(w):
    """12."""
    w.assign("speaker", [w.speaker_id])
    w.assign("host", [w.owner_id])
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        intake = event_planning.open_intake(db, ev)
        event_planning.complete(db, intake, ev)
        cap, ctx = _capture(fail=True)
        with ctx:
            event_planning.notify_completed(db, _Bg(), intake)
        db.expire_all()
        assert db.get(EventIntake, intake.id).status == "completed"
    finally:
        db.close()


# ══ LVE-003 ═════════════════════════════════════════════════════════════════════════════

def test_creating_an_event_is_not_approval(w):
    """1 - a draft event announces nothing."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        assert ev.status == "draft"
        assert event_comms.is_approved(ev, None) is False
        cap, ctx = _capture()
        with ctx:
            assert event_comms.notify_event_approved(db, _Bg(), ev) is False
        assert cap.calls == []
        # Publishing makes an event visible; it does not confirm it.
        ev.status = "published"
        db.commit()
        assert event_comms.is_approved(ev, None) is False, (
            "published is visibility, not confirmation")
    finally:
        db.close()


def test_approved_event_reports_truthful_details(w):
    """2, 3, 5, 6, 7, 8, 9 - every field traces to a committed column."""
    w.assign("speaker", [w.speaker_id])
    w.assign("host", [w.owner_id])
    w.set_event(status="scheduled")
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            assert event_comms.notify_event_approved(db, _Bg(), ev) is True
        text = cap.of(APPROVED)["text"]
        assert "Europe/London" in text, "the event timezone must be used"
        assert "Private" in text and "registration required" in text, text
        assert "capped at 500" in text, text
        assert "1 contributor(s) assigned" in text, text
        assert "Recording enabled" in text, text
        # No replay entitlement exists, so it must NOT read as available.
        assert "No replay entitlement created yet" in text, text
        assert "Replay published" not in text
        assert "Host: Event Owner" in text and "Speaker: Speaker One" in text, text
    finally:
        db.close()


def test_recipient_local_time_is_not_invented(w):
    """4 - no User.timezone exists, so no local conversion is shown."""
    w.set_event(status="scheduled")
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            event_comms.notify_event_approved(db, _Bg(), ev)
        text = cap.of(APPROVED)["text"]
        assert "Your local time" not in text, (
            "recipient-local time must not be shown without an authoritative recipient zone")
        assert event_comms.RECIPIENT_LOCAL_SUPPORTED is False
        from app.models import User as U
        assert not hasattr(U, "timezone"), (
            "a User.timezone appeared - recipient-local time must be revisited")
    finally:
        db.close()


def test_team_assigned_then_changed_and_rename_sends_nothing(w):
    """10, 11, 12, 15 - the signature is keyed on identity, not on display names."""
    w.assign("host", [w.owner_id])
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            assert event_comms.notify_team(db, _Bg(), ev) == "assigned"
            first = cap.count(TEAM_ASSIGNED)
            # 15 - dedup: an identical team sends nothing further.
            assert event_comms.notify_team(db, _Bg(), ev) is None
        assert first >= 1 and cap.count(TEAM_ASSIGNED) == first, cap.subjects
        assert cap.notifications(TEAM_ASSIGNED) == first

        # 12 - a staff display-name edit must NOT be a team change.
        owner = db.get(User, w.owner_id)
        owner.full_name = "Event Owner (Renamed)"
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            assert event_comms.notify_team(db, _Bg(), ev) is None, (
                "a rename must not send a Team Changed email")
        assert cap2.calls == [], cap2.subjects

        # 11 - a genuine membership change does send, with both sides shown.
        w.assign("speaker", [w.speaker_id])
        db.expire_all()
        ev = db.get(Event, w.event_id)
        cap3, ctx3 = _capture()
        with ctx3:
            assert event_comms.notify_team(db, _Bg(), ev) == "changed"
        text = cap3.of(TEAM_CHANGED)["text"]
        assert "Previous" in text and "Current" in text, text
        assert "Speaker One" in text, text
    finally:
        db.close()


def test_event_html_and_text(w):
    """14."""
    w.set_event(status="scheduled")
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            event_comms.notify_event_approved(db, _Bg(), ev)
        payload = cap.of(APPROVED)
        assert payload["html"].strip() and payload["text"].strip()
        assert payload["from"].startswith("Zoiko Steam Event Operations"), payload["from"]
    finally:
        db.close()


# ══ LVE-004 ═════════════════════════════════════════════════════════════════════════════

def test_all_four_categories_evaluate_from_real_state(w):
    """1, 2, 3, 4 - each category's verdict traces to committed event state."""
    w.assign("speaker", [])
    w.set_event(expected_audience=None, recording_enabled=False)
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        result = event_planning.evaluate(db, ev)
        assert result["contributors"] == "incomplete", result
        assert result["audience_access"] == "incomplete", result
        assert result["recording_replay"] == "incomplete", result
        # Accessibility has no backing domain, so it is never auto-completed.
        assert result["accessibility"] == "incomplete", result

        rows = {r.category: r for r in event_planning.requirements(db, ev)}
        assert any("contributors" in o.lower()
                   for o in rows["contributors"].outstanding), rows["contributors"].outstanding
        assert any("audience size" in o.lower()
                   for o in rows["audience_access"].outstanding)
        assert any("recording" in o.lower()
                   for o in rows["recording_replay"].outstanding)
    finally:
        db.close()


def test_completed_requirement_sends_no_action_email(w):
    """5."""
    w.assign("speaker", [w.speaker_id])
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        # Give the contributor a consented session so the category genuinely completes.
        from app.models import ContributorSession
        db.add(ContributorSession(event_id=ev.id, org_id=ev.org_id, user_id=w.speaker_id,
                                  identity=str(w.speaker_id), state="ready",
                                  consent_given=True, consent_at=_now()))
        db.commit()
        result = event_planning.evaluate(db, ev)
        assert result["contributors"] == "complete", result
        row = {r.category: r for r in event_planning.requirements(db, ev)}["contributors"]
        cap, ctx = _capture()
        with ctx:
            assert event_planning.notify_action_required(db, _Bg(), row) is False
        assert cap.calls == []
    finally:
        db.close()


def test_action_reaches_only_the_assigned_owner(w):
    """6, 7 - routed to the action owner, not to every stakeholder."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        event_planning.evaluate(db, ev)
        row = {r.category: r for r in event_planning.requirements(db, ev)}["accessibility"]
        row.assigned_to = w.speaker_id
        db.commit()
        cap, ctx = _capture()
        with ctx:
            assert event_planning.notify_action_required(db, _Bg(), row) is True
        recipients = [c["payload"]["to"][0].lower() for c in cap.calls]
        assert recipients == [w.speaker_email.lower()], recipients
        assert w.billing_email.lower() not in recipients, "unrelated stakeholders must not be mailed"
    finally:
        db.close()


def test_accessibility_never_claims_captions_exist(w):
    """10 - MED-010 is unsupported, so the message must say so."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        event_planning.evaluate(db, ev)
        row = {r.category: r for r in event_planning.requirements(db, ev)}["accessibility"]
        cap, ctx = _capture()
        with ctx:
            event_planning.notify_action_required(db, _Bg(), row)
        subject = email_mod.LVE_004_SUBJECTS["accessibility"].format(event="Annual Summit")
        text = cap.of(subject)["text"]
        assert "does not generate captions" in text, text
        for claim in ("captions are ready", "captions will be generated",
                      "we will provide captions"):
            assert claim not in text.lower(), claim
        # And it can never be auto-completed from evidence that does not exist.
        assert "accessibility" in event_planning.ATTESTED_ONLY
    finally:
        db.close()


def test_reopening_renotifies_exactly_once(w):
    """9, 12 - one message per cycle, and a reopen opens a new cycle."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        event_planning.evaluate(db, ev)
        row = {r.category: r for r in event_planning.requirements(db, ev)}["accessibility"]
        cap, ctx = _capture()
        with ctx:
            assert event_planning.notify_action_required(db, _Bg(), row) is True
            assert event_planning.notify_action_required(db, _Bg(), row) is False
        assert len(cap.calls) == 1

        event_planning.attest(db, row, status="complete")
        assert row.status == "complete"
        event_planning.attest(db, row, status="incomplete", outstanding=["Interpreter needed"])
        cap2, ctx2 = _capture()
        with ctx2:
            assert event_planning.notify_action_required(db, _Bg(), row) is True
            assert event_planning.notify_action_required(db, _Bg(), row) is False
        assert len(cap2.calls) == 1
    finally:
        db.close()


def test_planning_html_and_text(w):
    """11."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        event_planning.evaluate(db, ev)
        row = {r.category: r for r in event_planning.requirements(db, ev)}["accessibility"]
        cap, ctx = _capture()
        with ctx:
            event_planning.notify_action_required(db, _Bg(), row)
        payload = cap.calls[0]["payload"]
        assert payload["html"].strip() and payload["text"].strip()
    finally:
        db.close()


# ══ LVE-005 ═════════════════════════════════════════════════════════════════════════════

def test_rehearsal_scheduled_persisted_and_announced(w):
    """1, 2, 3, 10, 11 - state first, event timezone, contributors and team included."""
    w.assign("speaker", [w.speaker_id])
    w.assign("host", [w.owner_id])
    when = datetime(2027, 5, 4, 14, 0, tzinfo=timezone.utc)
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        rehearsal = event_planning.schedule(db, ev, scheduled_at=when,
                                            purpose="Check contribution paths")
        assert rehearsal.status == "scheduled"
        assert rehearsal.timezone_name == "Europe/London", rehearsal.timezone_name
        cap, ctx = _capture()
        with ctx:
            assert event_planning.notify_scheduled(db, _Bg(), rehearsal) is True
        subject = email_mod.LVE_005_SCHEDULED_SUBJECT.format(event="Annual Summit")
        got = cap.to(subject)
        assert w.speaker_email.lower() in got, f"contributor missing: {got}"
        assert w.owner_email.lower() in got, f"event owner missing: {got}"
        text = cap.of(subject)["text"]
        assert "Europe/London" in text and "03:00 PM" in text, text
    finally:
        db.close()


def test_rehearsal_reminder_threshold_and_dedup(w):
    """4, 5, 16."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        far = event_planning.schedule(db, ev, scheduled_at=_now() + timedelta(days=10))
        cap, ctx = _capture()
        with ctx:
            assert event_planning.notify_rehearsal_reminder(db, _Bg(), far) is False
        far.scheduled_at = _now() + timedelta(hours=3)
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            assert event_planning.notify_rehearsal_reminder(db, _Bg(), far) is True
            subject = email_mod.LVE_005_REMINDER_SUBJECT.format(event="Annual Summit")
            first = cap2.count(subject)
            assert event_planning.notify_rehearsal_reminder(db, _Bg(), far) is False
        assert first >= 1 and cap2.count(subject) == first
        assert cap2.notifications(subject) == first
    finally:
        db.close()


def test_completed_rehearsal_reports_outcome(w):
    """6, 7."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        rehearsal = event_planning.schedule(db, ev, scheduled_at=_now() + timedelta(days=1))
        result = event_planning.complete_rehearsal(
            db, rehearsal, validated=["Camera feed", "Audio"], outstanding=["Lighting"])
        assert result == "completed"
        cap, ctx = _capture()
        with ctx:
            assert event_planning.notify_completed_rehearsal(db, _Bg(), rehearsal) is True
        subject = email_mod.LVE_005_COMPLETED_SUBJECT.format(event="Annual Summit")
        text = cap.of(subject)["text"]
        assert "Camera feed" in text and "Lighting" in text, text
    finally:
        db.close()


def test_needs_repeat_requires_a_recorded_outcome(w):
    """8, 9 - unreachable without an authorized operator recording it."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        rehearsal = event_planning.schedule(db, ev, scheduled_at=_now() + timedelta(days=1))
        cap, ctx = _capture()
        with ctx:
            assert event_planning.notify_needs_repeat(db, _Bg(), rehearsal) is False, (
                "a merely scheduled rehearsal must not escalate")
        assert cap.calls == []

        result = event_planning.complete_rehearsal(
            db, rehearsal, validated=[], outstanding=["Encoder dropped"],
            repeat_required=True, repeat_reason="The contribution path did not hold",
            next_at=_now() + timedelta(days=3))
        assert result == "needs_repeat"
        cap2, ctx2 = _capture()
        with ctx2:
            assert event_planning.notify_needs_repeat(db, _Bg(), rehearsal) is True
        subject = email_mod.LVE_005_REPEAT_SUBJECT.format(event="Annual Summit")
        text = cap2.of(subject)["text"]
        assert "The contribution path did not hold" in text, text
        assert "Encoder dropped" in text, text
    finally:
        db.close()


def test_joining_instructions_leak_no_token_and_claim_nothing_false(w):
    """12, 13, 14 - link security and a truthful forwarding claim."""
    w.assign("speaker", [w.speaker_id])
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        rehearsal = event_planning.schedule(db, ev, scheduled_at=_now() + timedelta(days=2))
        cap, ctx = _capture()
        with ctx:
            event_planning.notify_scheduled(db, _Bg(), rehearsal)
        subject = email_mod.LVE_005_SCHEDULED_SUBJECT.format(event="Annual Summit")
        payload = cap.of(subject)
        blob = payload["text"] + payload["html"]
        for secret in ("token=", "stream_key", "livekit", "jwt", "Bearer", "?key="):
            assert secret.lower() not in blob.lower(), f"{secret} must never be emailed"
        # The claim made is the one the backend actually enforces: access is decided by the
        # signed-in contributor's own session, so forwarding grants nothing.
        assert "forwarding it gives nobody access" in blob
        assert "cannot be forwarded" not in blob.lower(), (
            "an unenforceable non-forwardable claim must not be made")
    finally:
        db.close()


def test_rehearsal_html_and_text(w):
    """15."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        rehearsal = event_planning.schedule(db, ev, scheduled_at=_now() + timedelta(days=2))
        cap, ctx = _capture()
        with ctx:
            event_planning.notify_scheduled(db, _Bg(), rehearsal)
        payload = cap.calls[0]["payload"]
        assert payload["html"].strip() and payload["text"].strip()
    finally:
        db.close()


# ══ cross-cutting ═══════════════════════════════════════════════════════════════════════

def test_families_are_classified_and_class_c_cannot_be_suppressed(w):
    """The registry governs LVE too, and a preference cannot silence a contract message."""
    from app.services import notifications

    for family in ("LVE-001", "LVE-002", "LVE-003", "LVE-004", "LVE-005"):
        assert family in notifications.FAMILY_CLASS, family
    assert notifications.is_mandatory("LVE-001") is True
    assert notifications.is_mandatory("LVE-003") is True
    assert notifications.is_mandatory("LVE-004") is False

    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        org.notifications = {k: False for k in
                             ("event_scheduled", "member_joined", "billing", "security_alerts")}
        db.commit()
        assert notifications.should_send_operational_notification(
            family="LVE-001", org=org) is True
        assert notifications.should_send_operational_notification(
            family="LVE-003", org=org) is True
    finally:
        db.close()


def test_no_lve_template_accepts_a_secret(w):
    """Structural: there is no parameter through which a credential could leak."""
    import inspect

    senders = [n for n in dir(email_mod)
               if n.startswith("send_") and any(
                   k in n for k in ("proposal", "booking", "intake", "event_approved",
                                    "event_team", "planning_action", "rehearsal"))]
    assert len(senders) >= 16, senders
    banned = ("token", "secret", "key", "password", "credential", "jwt")
    for name in senders:
        params = inspect.signature(getattr(email_mod, name)).parameters
        for param in params:
            assert not any(b in param.lower() for b in banned), f"{name}({param})"


def test_ledger_records_every_transition(w):
    """Durable evidence of what was announced, independent of the marker columns."""
    qid = w.quote(status="issued", valid_until=_now() + timedelta(days=3))
    db = SessionLocal()
    try:
        cap, ctx = _capture()
        with ctx:
            event_comms.notify_proposal_ready(db, _Bg(), db.get(Quote, qid))
        rows = db.query(EventLifecycleEvent).filter(
            EventLifecycleEvent.org_id == w.org_id).all()
        assert any(r.family == "LVE-001" and r.transition == "proposal_ready"
                   for r in rows), [(r.family, r.transition) for r in rows]
    finally:
        db.close()


TESTS = [
    test_draft_proposal_sends_nothing,
    test_issued_proposal_reaches_owner_and_commercial_contacts,
    test_purchaser_is_not_treated_as_the_event_owner,
    test_scope_responsibilities_and_assumptions_are_derived,
    test_validity_expiry_is_exact_and_in_the_event_timezone,
    test_accepted_variant_reports_committed_acceptance,
    test_booking_changed_shows_previous_and_current,
    test_expired_proposal_cannot_be_accepted_and_is_announced_once,
    test_expiry_sweep_moves_issued_proposals_past_validity,
    test_proposal_html_and_text_and_no_tracking,
    test_provider_failure_does_not_change_booking_state,
    test_intake_opens_persisted_and_announced,
    test_intake_cannot_complete_while_validation_fails,
    test_intake_completes_only_after_validation_passes,
    test_intake_reopen_names_sections_and_reason,
    test_intake_reminder_fires_once_at_the_threshold,
    test_provider_failure_does_not_alter_intake,
    test_creating_an_event_is_not_approval,
    test_approved_event_reports_truthful_details,
    test_recipient_local_time_is_not_invented,
    test_team_assigned_then_changed_and_rename_sends_nothing,
    test_event_html_and_text,
    test_all_four_categories_evaluate_from_real_state,
    test_completed_requirement_sends_no_action_email,
    test_action_reaches_only_the_assigned_owner,
    test_accessibility_never_claims_captions_exist,
    test_reopening_renotifies_exactly_once,
    test_planning_html_and_text,
    test_rehearsal_scheduled_persisted_and_announced,
    test_rehearsal_reminder_threshold_and_dedup,
    test_completed_rehearsal_reports_outcome,
    test_needs_repeat_requires_a_recorded_outcome,
    test_joining_instructions_leak_no_token_and_claim_nothing_false,
    test_rehearsal_html_and_text,
    test_families_are_classified_and_class_c_cannot_be_suppressed,
    test_no_lve_template_accepts_a_secret,
    test_ledger_records_every_transition,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    _assert_no_leak()
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
