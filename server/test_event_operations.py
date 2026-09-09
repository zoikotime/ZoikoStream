"""LVE-006 -> LVE-012 - readiness, event day, incidents, completion, evidence (ZST-EC-001).

The five assertions the spec calls out as most important each have a dedicated test here:

    LVE-008  a schedule change is never silent          test_schedule_change_is_never_silent
    LVE-006  blocked readiness reaches the owner        test_blocked_readiness_reaches_owner
    LVE-010  cancel is not inferred from broadcast end  test_cancellation_is_never_inferred
    LVE-009  activation sends no audience mail          test_activation_never_reaches_audience
    LVE-011  no premature recording-completeness claim  test_ended_does_not_claim_recording_done

Run with `python test_event_operations.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app.email as email_mod
from app.db import SessionLocal
from app.models import (
    CommercialAccount,
    Event,
    EventAssignment,
    EventBrief,
    EventIncident,
    EventLifecycleEvent,
    EventReadinessState,
    EventRegistration,
    EventScheduleChange,
    LiveRecording,
    Organization,
    PostEventReport,
    User,
)
from app.security import hash_password
from app.services import event_closeout, event_comms, event_ops

PASSWORD = "correct-horse-battery"

READY_PASSED = email_mod.LVE_006_PASSED_SUBJECT
READY_BLOCKED = email_mod.LVE_006_BLOCKED_SUBJECT
READY_REGRESSED = email_mod.LVE_006_REGRESSED_SUBJECT
BRIEF = email_mod.LVE_007_SUBJECT
SCHEDULE = email_mod.LVE_008_SUBJECT
ARMED = email_mod.LVE_009_ARMED_SUBJECT
LIVE = email_mod.LVE_009_LIVE_SUBJECT
ENDED = email_mod.LVE_011_ENDED_SUBJECT
REPORT_READY = email_mod.LVE_012_READY_SUBJECT
CLOSED = email_mod.LVE_012_CLOSED_SUBJECT


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


def _new_email(tag="ops"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


def _now():
    return datetime.now(timezone.utc)


class World:
    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Ops Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata")
            db.add(org)
            db.flush()
            self.org_id = org.id
            self.owner_email = _new_email("owner")
            self.owner_id = self._u(db, "host", self.owner_email, "Event Owner")
            self.admin_email = _new_email("admin")
            self.admin_id = self._u(db, "org_admin", self.admin_email, "Org Admin")
            self.speaker_email = _new_email("speaker")
            self.speaker_id = self._u(db, "viewer", self.speaker_email, "Speaker One")
            self.billing_email = _new_email("billing")
            org.owner_user_id = self.admin_id

            account = CommercialAccount(org_id=org.id,
                                        billing_contact_email=self.billing_email,
                                        billing_contact_name="Billing Desk")
            db.add(account)
            db.flush()
            self.account_id = account.id

            ev = Event(org_id=org.id, created_by=self.owner_id, title="Ops Summit",
                       status="draft", timezone="Europe/London",
                       start_time=_now() + timedelta(days=20),
                       end_time=_now() + timedelta(days=20, hours=2),
                       visibility="private", registration_required=True,
                       registration_limit=200, expected_audience=None,
                       recording_enabled=True, commercial_account_id=account.id)
            db.add(ev)
            db.flush()
            self.event_id = ev.id
            db.add(EventAssignment(event_id=ev.id, user_id=self.owner_id, role="host"))
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

    def set_event(self, **fields):
        db = SessionLocal()
        try:
            ev = db.get(Event, self.event_id)
            for k, v in fields.items():
                setattr(ev, k, v)
            db.commit()
        finally:
            db.close()

    def register(self, n=2):
        db = SessionLocal()
        try:
            for i in range(n):
                db.add(EventRegistration(event_id=self.event_id,
                                         name=f"Attendee {i}", email=_new_email("att")))
            db.commit()
        finally:
            db.close()

    def recording(self, **fields):
        db = SessionLocal()
        try:
            rec = LiveRecording(event_id=self.event_id, org_id=self.org_id,
                                status=fields.pop("status", "stopped"),
                                started_at=_now() - timedelta(hours=1),
                                stopped_at=_now(), enforced=True, **fields)
            db.add(rec)
            db.commit()
            return rec.id
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            for model in (EventBrief, EventReadinessState, EventScheduleChange,
                          PostEventReport):
                db.query(model).filter(model.event_id == self.event_id).delete()
            from app.models import EventActivationState, EventCompletionState
            for model in (EventActivationState, EventCompletionState):
                db.query(model).filter(model.event_id == self.event_id).delete()
            db.query(EventLifecycleEvent).filter(
                EventLifecycleEvent.org_id == self.org_id).delete()
            db.query(EventIncident).filter(EventIncident.event_id == self.event_id).delete()
            db.query(LiveRecording).filter(
                LiveRecording.event_id == self.event_id).delete()
            db.query(EventRegistration).filter(
                EventRegistration.event_id == self.event_id).delete()
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


# ══ LVE-006 ═════════════════════════════════════════════════════════════════════════════

def test_readiness_reuses_the_existing_engine(w):
    """1 - no readiness rule is reimplemented in the communication layer."""
    import inspect

    source = inspect.getsource(event_ops)
    assert "evaluate_readiness" in source, "the engine must be called"
    # The communication layer must not carry its own check vocabulary or gate list.
    for rule in ("required_readiness_checks", "capacity_confirmed", "PRODUCTION_EVENT_STATES",
                 "envelope_capacity_block_reason"):
        assert rule not in source, f"readiness rule {rule!r} reimplemented in event_ops"


def test_same_state_polling_sends_nothing(w):
    """2, 11 - the console polls readiness; a held position must stay silent."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            first = event_ops.notify_readiness(db, _Bg(), ev)
            before = len(cap.calls)
            for _ in range(3):
                assert event_ops.notify_readiness(db, _Bg(), ev) is None
        assert first is not None, "the first evaluation is a transition"
        assert len(cap.calls) == before, "repeated polling must not resend"
    finally:
        db.close()


def test_blocked_readiness_reaches_owner(w):
    """3, 4, 5, 6, 9 - the customer owner and team learn an event is blocked."""
    w.set_event(billing_classification="commercial", risk_tier="r2")
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            variant = event_ops.notify_readiness(db, _Bg(), ev)
        assert variant == "blocked", variant
        subject = READY_BLOCKED.format(event="Ops Summit")
        got = cap.to(subject)
        assert w.owner_email.lower() in got, f"event owner missing: {got}"
        text = cap.of(subject)["text"]
        # The conditions shown are the engine's own reasons.
        row = db.scalar(select_readiness(ev.id))
        for reason in (row.blocking_reasons or [])[:1]:
            assert reason[:30] in text, text
        assert "May the event proceed?: No" in text, text
    finally:
        db.close()


def select_readiness(event_id):
    from sqlalchemy import select
    return select(EventReadinessState).where(EventReadinessState.event_id == event_id)


def test_passed_only_from_authoritative_pass(w):
    """7 - a pass is the engine's verdict, never an inspection of the UI."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            variant = event_ops.notify_readiness(db, _Bg(), ev)
        # A plain non-commercial event has no required checks, so the engine passes it.
        assert variant == "passed", variant
        assert cap.count(READY_PASSED.format(event="Ops Summit")) >= 1
    finally:
        db.close()


def test_passed_then_blocked_produces_regressed(w):
    """8 - a Passed message must never be left standing once readiness is unsafe."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            assert event_ops.notify_readiness(db, _Bg(), ev) == "passed"
        # Make it commercial without a profile: the engine now blocks it.
        ev.billing_classification, ev.risk_tier = "commercial", "r2"
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            variant = event_ops.notify_readiness(db, _Bg(), ev)
        assert variant == "regressed", variant
        subject = READY_REGRESSED.format(event="Ops Summit")
        text = cap2.of(subject)["text"]
        assert "Previous readiness" in text and "Passed" in text, text
    finally:
        db.close()


def test_readiness_leaks_no_internal_detection_detail(w):
    """10 - conditions are shown; risk scores and detection logic are not."""
    w.set_event(billing_classification="commercial", risk_tier="r2")
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            event_ops.notify_readiness(db, _Bg(), ev)
        blob = "".join(c["payload"]["text"] + c["payload"]["html"] for c in cap.calls).lower()
        for secret in ("risk_score", "livekit", "token=", "stream_key", "/admin",
                       "super_admin"):
            assert secret not in blob, f"{secret} must not appear"
    finally:
        db.close()


def test_provider_failure_does_not_affect_readiness(w):
    """12."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture(fail=True)
        with ctx:
            event_ops.notify_readiness(db, _Bg(), ev)
        db.expire_all()
        row = db.scalar(select_readiness(w.event_id))
        assert row is not None and row.variant == "passed", "state must survive an outage"
    finally:
        db.close()


# ══ LVE-008 ═════════════════════════════════════════════════════════════════════════════

def test_noop_update_sends_nothing(w):
    """1 - a save that does not move the schedule is not a schedule change."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            change = event_ops.record_change(
                db, ev, previous_start=ev.start_time, previous_end=ev.end_time,
                previous_timezone=ev.timezone)
        assert change is None, "an unchanged window must not create a record"
        assert cap.calls == []
    finally:
        db.close()


def test_schedule_change_is_never_silent(w):
    """2, 3, 4, 5, 6, 7 - the SEV-1 case: a moved start_time always leaves a record."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        previous = ev.start_time
        ev.start_time = previous + timedelta(days=2)
        db.commit()
        cap, ctx = _capture()
        with ctx:
            change = event_ops.record_change(db, ev, previous_start=previous,
                                             previous_end=None, previous_timezone="Europe/London",
                                             actor_id=w.admin_id)
            assert change is not None, "a moved schedule MUST persist a record"
            event_ops.notify_schedule_change(db, _Bg(), ev, change)
        assert change.previous_start_time == previous
        assert change.new_start_time == ev.start_time
        assert change.date_changed is True

        subject = SCHEDULE.format(event="Ops Summit")
        got = cap.to(subject)
        assert w.owner_email.lower() in got, f"owner missing: {got}"
        text = cap.of(subject)["text"]
        assert "Europe/London" in text, "the event timezone must be used"
        assert "Previous" in text and "New" in text, text
    finally:
        db.close()


def test_downstream_domains_are_reevaluated(w):
    """10, 11, 12 - and absent subsystems are reported, not faked."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        previous = ev.start_time
        ev.start_time = previous + timedelta(days=3)
        db.commit()
        cap, ctx = _capture()
        with ctx:
            change = event_ops.record_change(db, ev, previous_start=previous,
                                             previous_end=None, previous_timezone=ev.timezone)
        downstream = change.downstream or {}
        assert "readiness" in downstream and "re-evaluated" in downstream["readiness"]
        assert "planning" in downstream and downstream["planning"] == "re-evaluated"
        assert "rehearsals" in downstream
        # Absent subsystems must say so rather than claim an update.
        assert "not supported" in downstream["audience_reminders"]
        assert "not supported" in downstream["access_windows"]
    finally:
        db.close()


def test_audience_is_only_notified_when_governed(w):
    """9 - an internal planning change stays internal."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        previous = ev.start_time
        ev.start_time = previous + timedelta(days=1)
        db.commit()
        change = event_ops.record_change(db, ev, previous_start=previous,
                                         previous_end=None, previous_timezone=ev.timezone)
        # Draft event, no registrations: both gates refuse.
        allowed, why = event_ops.audience_decision(db, ev, change)
        assert allowed is False, why
        assert "no audience registrations" in why

        w.register(2)
        db.expire_all()
        ev = db.get(Event, w.event_id)
        allowed, why = event_ops.audience_decision(db, ev, change)
        assert allowed is False, "a draft event is not published to an audience"

        ev.status = "scheduled"
        db.commit()
        allowed, why = event_ops.audience_decision(db, ev, change)
        assert allowed is True, why
    finally:
        db.close()


def test_duplicate_schedule_notification_is_safe(w):
    """13 - each recipient class has its own single-shot marker."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        previous = ev.start_time
        ev.start_time = previous + timedelta(days=4)
        db.commit()
        change = event_ops.record_change(db, ev, previous_start=previous,
                                         previous_end=None, previous_timezone=ev.timezone)
        cap, ctx = _capture()
        with ctx:
            first = event_ops.notify_schedule_change(db, _Bg(), ev, change)
            count = len(cap.calls)
            second = event_ops.notify_schedule_change(db, _Bg(), ev, change)
        assert first["owner"] is True and second["owner"] is False
        assert len(cap.calls) == count, "a repeat must add no messages"
    finally:
        db.close()


def test_provider_failure_does_not_revert_start_time(w):
    """14."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        previous = ev.start_time
        moved = previous + timedelta(days=5)
        ev.start_time = moved
        db.commit()
        change = event_ops.record_change(db, ev, previous_start=previous,
                                         previous_end=None, previous_timezone=ev.timezone)
        cap, ctx = _capture(fail=True)
        with ctx:
            event_ops.notify_schedule_change(db, _Bg(), ev, change)
        db.expire_all()
        assert db.get(Event, w.event_id).start_time == moved
    finally:
        db.close()


# ══ LVE-009 ═════════════════════════════════════════════════════════════════════════════

def test_armed_and_live_come_from_real_state(w):
    """1, 3, 6 - and a duplicate transition is safe."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            assert event_ops.notify_activation(db, _Bg(), ev) is None, (
                "a draft event is neither armed nor live")
        assert cap.calls == []

        ev.status = "armed"
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            assert event_ops.notify_activation(db, _Bg(), ev) == "armed"
            count = len(cap2.calls)
            assert event_ops.notify_activation(db, _Bg(), ev) is None
        assert len(cap2.calls) == count
        assert cap2.count(ARMED.format(event="Ops Summit")) >= 1

        ev.status = "live"
        db.commit()
        cap3, ctx3 = _capture()
        with ctx3:
            assert event_ops.notify_activation(db, _Bg(), ev) == "live"
        assert cap3.count(LIVE.format(event="Ops Summit")) >= 1
    finally:
        db.close()


def test_activation_never_reaches_audience(w):
    """7 - internal go-live must not mail a single registrant."""
    w.register(3)
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        ev.status = "live"
        db.commit()
        cap, ctx = _capture()
        with ctx:
            event_ops.notify_activation(db, _Bg(), ev)
        recipients = {c["payload"]["to"][0].lower() for c in cap.calls}
        attendees = {r.email.lower() for r in db.query(EventRegistration).filter(
            EventRegistration.event_id == w.event_id).all()}
        assert not (recipients & attendees), (
            f"internal activation reached the audience: {recipients & attendees}")
        # Structural: the module cannot even reach an audience sender.
        import inspect
        source = inspect.getsource(event_ops.notify_activation)
        for audience_sender in ("send_viewer_invite_email", "send_registration_confirmation",
                                "send_replay_available_email"):
            assert audience_sender not in source
    finally:
        db.close()


def test_unsupported_activation_variants_are_reported(w):
    """2, 4 - no audience-access schedule and no MONITORING state exist."""
    from app.models import EVENT_STATUSES

    assert event_ops.AUDIENCE_ACCESS_SCHEDULE_SUPPORTED is False
    assert event_ops.MONITORING_STATE_SUPPORTED is False
    assert "monitoring" not in EVENT_STATUSES, (
        "a monitoring state appeared; LVE-009 must be revisited")
    # And no template exists for either, so neither can be sent by accident.
    assert not hasattr(email_mod, "send_audience_access_opening_email")


def test_activation_html_and_text(w):
    """8."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        ev.status = "armed"
        db.commit()
        cap, ctx = _capture()
        with ctx:
            event_ops.notify_activation(db, _Bg(), ev)
        payload = cap.calls[0]["payload"]
        assert payload["html"].strip() and payload["text"].strip()
        assert "No audience communication was sent" in payload["text"]
    finally:
        db.close()


# ══ LVE-010 ═════════════════════════════════════════════════════════════════════════════

def test_delayed_and_hold_persist_and_notify(w):
    """2, 3, 4, 5, 10 - with a coarse, customer-safe reason category."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        cap, ctx = _capture()
        with ctx:
            incident = event_ops.open_incident(
                db, ev, state="delayed", reason_category="technical",
                summary="A contribution path is being restored.")
            assert incident is not None and incident.operational_state == "delayed"
            assert event_ops.notify_incident(db, _Bg(), ev, incident) == "delayed"
        text = cap.of(email_mod.LVE_010_DELAYED_SUBJECT.format(event="Ops Summit"))["text"]
        assert "Technical" in text, text

        cap2, ctx2 = _capture()
        with ctx2:
            incident = event_ops.open_incident(db, ev, state="temporary_hold",
                                               reason_category="operational")
            assert event_ops.notify_incident(db, _Bg(), ev, incident) == "temporary_hold"
        assert cap2.count(email_mod.LVE_010_HOLD_SUBJECT.format(event="Ops Summit")) >= 1
    finally:
        db.close()


def test_resume_requires_an_interruption(w):
    """1, 6, 7 - and a pause is not a cancellation."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        incident = event_ops.open_incident(db, ev, state="delayed",
                                           reason_category="technical")
        assert event_ops.resume_incident(db, incident) is True
        assert incident.operational_state == "resumed"
        cap, ctx = _capture()
        with ctx:
            assert event_ops.notify_incident(db, _Bg(), ev, incident) == "resumed"
        text = cap.of(email_mod.LVE_010_RESUMED_SUBJECT.format(event="Ops Summit"))["text"]
        assert "Interruption duration" in text, text
        # An event that was merely delayed is not cancelled anywhere.
        db.expire_all()
        assert db.get(Event, w.event_id).status != "cancelled"
    finally:
        db.close()


def test_cancellation_is_never_inferred(w):
    """8, 9 - cancel requires the EVENT to be authoritatively cancelled."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        incident = event_ops.open_incident(db, ev, state="delayed",
                                           reason_category="technical")
        # Every ordinary end-of-broadcast condition, none of which is a cancellation.
        for status_ in ("live", "ending", "processing", "ended"):
            ev.status = status_
            db.commit()
            assert event_ops.cancel_incident(
                db, ev, incident, reason_category="technical") is False, (
                f"'{status_}' must never be treated as a cancellation")
        cap, ctx = _capture()
        with ctx:
            assert event_ops.notify_incident(db, _Bg(), ev, incident) != "canceled"

        ev.status = "cancelled"
        db.commit()
        assert event_ops.cancel_incident(db, ev, incident, reason_category="operational",
                                         summary="The customer withdrew the event.") is True
        cap2, ctx2 = _capture()
        with ctx2:
            assert event_ops.notify_incident(db, _Bg(), ev, incident) == "canceled"
        assert cap2.count(email_mod.LVE_010_CANCELED_SUBJECT.format(event="Ops Summit")) >= 1
    finally:
        db.close()


def test_next_update_promised_only_when_committed(w):
    """11 - no default, no derived time, no "shortly"."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        incident = event_ops.open_incident(db, ev, state="delayed",
                                           reason_category="technical")
        cap, ctx = _capture()
        with ctx:
            event_ops.notify_incident(db, _Bg(), ev, incident)
        text = cap.calls[0]["payload"]["text"]
        assert "Next update" not in text, "no update may be promised without a committed time"
        for phrase in ("we will update you by", "shortly", "as soon as possible"):
            assert phrase not in text.lower(), phrase

        promised = _now() + timedelta(hours=1)
        incident2 = event_ops.open_incident(db, ev, state="temporary_hold",
                                            reason_category="technical",
                                            next_update_at=promised)
        cap2, ctx2 = _capture()
        with ctx2:
            event_ops.notify_incident(db, _Bg(), ev, incident2)
        assert "Next update" in cap2.calls[0]["payload"]["text"]
    finally:
        db.close()


def test_incident_hides_investigation_detail_and_dedups(w):
    """12, 13, 14."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        incident = event_ops.open_incident(db, ev, state="delayed",
                                           reason_category="safety_security")
        incident.evidence = {"root_cause": "operator credential compromise",
                             "internal_ticket": "SEC-42"}
        db.commit()
        cap, ctx = _capture()
        with ctx:
            assert event_ops.notify_incident(db, _Bg(), ev, incident) == "delayed"
            count = len(cap.calls)
            assert event_ops.notify_incident(db, _Bg(), ev, incident) is None
        assert len(cap.calls) == count, "dedup"
        blob = "".join(c["payload"]["text"] + c["payload"]["html"] for c in cap.calls).lower()
        for leak in ("root_cause", "credential compromise", "sec-42"):
            assert leak not in blob, f"investigation detail leaked: {leak}"
        assert "safety and security" in blob
    finally:
        db.close()


# ══ LVE-011 ═════════════════════════════════════════════════════════════════════════════

def test_ended_does_not_claim_recording_done(w):
    """1, 2, 3 - the boundary this family exists to preserve."""
    w.recording(status="stopped", validation_status=None)
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        ev.status, ev.end_time = "ended", _now()
        db.commit()
        cap, ctx = _capture()
        with ctx:
            assert event_closeout.notify_event_ended(db, _Bg(), ev) is True
        text = cap.of(ENDED)["text"]
        # Stopped is NOT finalized.
        assert "Recording processing is still in progress" in text, text
        for claim in ("recording complete", "recording is ready", "replay is available",
                      "your replay is ready"):
            assert claim not in text.lower(), claim
    finally:
        db.close()


def test_recording_position_tracks_finalization(w):
    """3 - each step reports only what the evidence supports."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        assert "No recording" in event_closeout.recording_position(db, ev)
        rid = w.recording(status="recording")
        db.expire_all()
        assert "still in progress" in event_closeout.recording_position(db, ev)
        rec = db.get(LiveRecording, rid)
        rec.status = "stopped"
        db.commit()
        assert "processing is still in progress" in event_closeout.recording_position(db, ev)
        rec.validation_status = "valid"
        db.commit()
        assert "finalized and validated" in event_closeout.recording_position(db, ev)
    finally:
        db.close()


def test_replay_variants_and_recipients(w):
    """4, 5, 6, 7, 8, 9, 10 - and the purchaser is never substituted."""
    from app.crud import commercial as commercial_crud

    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        ent = commercial_crud.get_or_create_replay_entitlement(db, ev, scope="audience")
        ent.publish_state = "ready_for_review"
        db.commit()
        cap, ctx = _capture()
        with ctx:
            assert event_closeout.notify_replay(db, _Bg(), ev) == "replay_approval_required"
            count = len(cap.calls)
            assert event_closeout.notify_replay(db, _Bg(), ev) is None  # dedup
        assert len(cap.calls) == count
        got = cap.to(email_mod.LVE_011_APPROVAL_SUBJECT)
        assert w.owner_email.lower() in got, f"event owner missing: {got}"
        assert w.billing_email.lower() not in got, (
            "the billing contact must not receive the replay notice")

        ent.publish_state = "published"
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            assert event_closeout.notify_replay(db, _Bg(), ev) == "replay_published"

        ent.publish_state = "withheld"
        db.commit()
        cap3, ctx3 = _capture()
        with ctx3:
            assert event_closeout.notify_replay(db, _Bg(), ev) == "replay_unavailable"
        assert cap3.count(email_mod.LVE_011_UNAVAILABLE_SUBJECT) >= 1
        db.query(type(ent)).filter(type(ent).id == ent.id).delete()
        db.commit()
    finally:
        db.close()


def test_completion_html_and_text(w):
    """11."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        ev.status, ev.end_time = "ended", _now()
        db.commit()
        cap, ctx = _capture()
        with ctx:
            event_closeout.notify_event_ended(db, _Bg(), ev)
        payload = cap.of(ENDED)
        assert payload["html"].strip() and payload["text"].strip()
    finally:
        db.close()


# ══ LVE-007 ═════════════════════════════════════════════════════════════════════════════

def test_draft_brief_sends_nothing_and_approval_persists(w):
    """1, 2, 3, 4, 12."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        brief = event_closeout.create_brief(db, ev, actor_id=w.admin_id)
        assert brief.status == "draft"
        cap, ctx = _capture()
        with ctx:
            assert event_closeout.notify_brief_approved(db, _Bg(), brief) is False
        assert cap.calls == []

        assert event_closeout.approve_brief(db, ev, brief, actor_id=w.admin_id) is True
        assert brief.status == "approved" and brief.approved_at is not None
        cap2, ctx2 = _capture()
        with ctx2:
            assert event_closeout.notify_brief_approved(db, _Bg(), brief) is True
            count = len(cap2.calls)
            assert event_closeout.notify_brief_approved(db, _Bg(), brief) is False
        assert len(cap2.calls) == count
        assert "Brief version: 1" in cap2.of(BRIEF)["text"]
    finally:
        db.close()


def test_superseded_brief_and_timezone(w):
    """5, 6, 7, 10, 11."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        first = event_closeout.create_brief(db, ev, actor_id=w.admin_id)
        event_closeout.approve_brief(db, ev, first, actor_id=w.admin_id)
        second = event_closeout.create_brief(db, ev, actor_id=w.admin_id)
        event_closeout.approve_brief(db, ev, second, actor_id=w.admin_id)
        db.expire_all()
        assert db.get(EventBrief, first.id).status == "superseded"
        assert db.get(EventBrief, second.id).status == "approved"

        cap, ctx = _capture()
        with ctx:
            event_closeout.notify_brief_approved(db, _Bg(), second)
        payload = cap.of(BRIEF)
        assert "Europe/London" in payload["text"], "the event timezone must appear"
        assert "Your local time" not in payload["text"], (
            "no recipient-local time without an authoritative recipient zone")
        assert w.owner_email.lower() in cap.to(BRIEF)
        assert payload["html"].strip() and payload["text"].strip()
    finally:
        db.close()


def test_brief_cta_is_authorized_and_not_admin(w):
    """8, 9 - a console link, not a public artifact and not Super Admin."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        brief = event_closeout.create_brief(db, ev, actor_id=w.admin_id)
        event_closeout.approve_brief(db, ev, brief, actor_id=w.admin_id)
        cap, ctx = _capture()
        with ctx:
            event_closeout.notify_brief_approved(db, _Bg(), brief)
        blob = cap.of(BRIEF)["text"] + cap.of(BRIEF)["html"]
        assert f"/organization/events/{w.event_id}" in blob
        for bad in ("/admin", "super_admin", "token=", "signed_url", "storage.googleapis"):
            assert bad not in blob, f"{bad} must not appear in a customer CTA"
    finally:
        db.close()


# ══ LVE-012 ═════════════════════════════════════════════════════════════════════════════

def test_report_state_persists_and_notifies(w):
    """1, 2, 3, 4, 10, 11, 12."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        row = event_closeout.open_report(db, ev, actor=None)
        assert row.status == "processing" and row.report_version == 1
        status_ = event_closeout.generate(db, ev, row, actor=None)
        assert status_ in ("ready", "failed"), status_
        cap, ctx = _capture()
        with ctx:
            assert event_closeout.notify_report(db, _Bg(), ev, row) == status_
            count = len(cap.calls)
            assert event_closeout.notify_report(db, _Bg(), ev, row) is None
        assert len(cap.calls) == count, "dedup"
        if status_ == "ready":
            got = cap.to(REPORT_READY)
            assert w.owner_email.lower() in got
            assert w.billing_email.lower() not in got, (
                "a billing contact is not an authorized analyst")
            payload = cap.of(REPORT_READY)
            assert payload["html"].strip() and payload["text"].strip()
    finally:
        db.close()


def test_privacy_threshold_is_not_invented(w):
    """5, 6 - no segment breakdown, and the absent policy is recorded as absent."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        row = event_closeout.open_report(db, ev, actor=None)
        event_closeout.generate(db, ev, row, actor=None)
        assert event_closeout.PRIVACY_POLICY_AVAILABLE is False
        assert row.privacy_suppression_applied is False, (
            "suppression must not be claimed while no policy exists")
        cap, ctx = _capture()
        with ctx:
            event_closeout.notify_report(db, _Bg(), ev, row)
        if cap.calls:
            blob = "".join(c["payload"]["text"] for c in cap.calls).lower()
            # No fabricated threshold anywhere in the copy.
            for invented in ("minimum 5", "at least 5 viewers", "k-anonym",
                             "fewer than 5", "suppressed for privacy"):
                assert invented not in blob, f"invented privacy policy: {invented}"
            assert "no approved disclosure threshold" in blob
    finally:
        db.close()


def test_incident_review_requires_approval(w):
    """7 - drafts and root-cause notes are never mailed."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        incident = event_ops.open_incident(db, ev, state="delayed",
                                           reason_category="technical",
                                           summary="A contribution path was restored.")
        incident.evidence = {"root_cause": "misconfigured encoder"}
        db.commit()
        cap, ctx = _capture()
        with ctx:
            assert event_closeout.notify_incident_review(db, _Bg(), ev, incident) is False, (
                "a pending review must not be mailed")
        assert cap.calls == []

        incident.review_state = "approved"
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            assert event_closeout.notify_incident_review(db, _Bg(), ev, incident) is True
        blob = "".join(c["payload"]["text"] for c in cap2.calls).lower()
        assert "misconfigured encoder" not in blob, "root-cause detail must not be mailed"
    finally:
        db.close()


def test_retention_is_read_from_med_011(w):
    """8 - no second retention system."""
    import inspect

    rid = w.recording(status="stopped", legal_hold=True,
                      retention_expires_at=_now() + timedelta(days=30))
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        position = event_closeout.retention_position(db, ev)
        assert position["legal_hold"] == 1, position
        assert position["earliest_expiry"] is not None
        source = inspect.getsource(event_closeout)
        # The retention columns are READ; none is written here.
        for write in ("retention_expires_at =", "legal_hold =", "deletion_status ="):
            assert write not in source, f"event_closeout must not write {write!r}"
    finally:
        db.close()


def test_record_closed_never_claims_erasure(w):
    """9."""
    w.recording(status="stopped")
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        row = event_closeout.open_report(db, ev, actor=None)
        event_closeout.generate(db, ev, row, actor=None)
        if row.status != "ready":
            return
        cap, ctx = _capture()
        with ctx:
            assert event_closeout.notify_record_closed(db, _Bg(), ev, row) is True
        text = cap.of(CLOSED)["text"]
        for claim in ("all records were deleted", "permanently erased",
                      "all copies removed", "everything has been deleted"):
            assert claim not in text.lower(), claim
        assert "are retained under their own policies" in text
    finally:
        db.close()


# ══ cross-cutting ═══════════════════════════════════════════════════════════════════════

def test_families_are_classified(w):
    from app.services import notifications

    for family in ("LVE-006", "LVE-007", "LVE-008", "LVE-009", "LVE-010", "LVE-011",
                   "LVE-012"):
        assert family in notifications.FAMILY_CLASS, family
    # A blocked event, a moved schedule and a cancellation must never be suppressible.
    for mandatory in ("LVE-006", "LVE-008", "LVE-010", "LVE-011"):
        assert notifications.is_mandatory(mandatory) is True, mandatory


def test_no_template_accepts_a_secret(w):
    import inspect

    senders = ["send_readiness_email", "send_event_brief_email", "send_schedule_change_email",
               "send_activation_email", "send_event_incident_email", "send_event_ended_email",
               "send_replay_lifecycle_email", "send_post_event_report_email",
               "send_incident_review_email", "send_record_closed_email"]
    banned = ("token", "secret", "key", "password", "credential", "jwt", "livekit")
    for name in senders:
        for param in inspect.signature(getattr(email_mod, name)).parameters:
            assert not any(b in param.lower() for b in banned), f"{name}({param})"


TESTS = [
    test_readiness_reuses_the_existing_engine,
    test_same_state_polling_sends_nothing,
    test_blocked_readiness_reaches_owner,
    test_passed_only_from_authoritative_pass,
    test_passed_then_blocked_produces_regressed,
    test_readiness_leaks_no_internal_detection_detail,
    test_provider_failure_does_not_affect_readiness,
    test_noop_update_sends_nothing,
    test_schedule_change_is_never_silent,
    test_downstream_domains_are_reevaluated,
    test_audience_is_only_notified_when_governed,
    test_duplicate_schedule_notification_is_safe,
    test_provider_failure_does_not_revert_start_time,
    test_armed_and_live_come_from_real_state,
    test_activation_never_reaches_audience,
    test_unsupported_activation_variants_are_reported,
    test_activation_html_and_text,
    test_delayed_and_hold_persist_and_notify,
    test_resume_requires_an_interruption,
    test_cancellation_is_never_inferred,
    test_next_update_promised_only_when_committed,
    test_incident_hides_investigation_detail_and_dedups,
    test_ended_does_not_claim_recording_done,
    test_recording_position_tracks_finalization,
    test_replay_variants_and_recipients,
    test_completion_html_and_text,
    test_draft_brief_sends_nothing_and_approval_persists,
    test_superseded_brief_and_timezone,
    test_brief_cta_is_authorized_and_not_admin,
    test_report_state_persists_and_notifies,
    test_privacy_threshold_is_not_invented,
    test_incident_review_requires_approval,
    test_retention_is_read_from_med_011,
    test_record_closed_never_claims_erasure,
    test_families_are_classified,
    test_no_template_accepts_a_secret,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
