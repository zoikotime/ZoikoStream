"""CON-001 -> CON-005 - event contributor authorization (ZST-EC-001).

The assertions the spec calls out as most important each have a dedicated test:

    CON-001  external contributor needs no membership   test_external_contributor_needs_no_membership
    CON-001  CTA respects the backstage boundary        test_cta_never_reaches_an_organizer_console
    CON-002  checks are real, not email-only            test_technical_check_uses_real_browser_results
    CON-003  durable scheduling, survives restart       test_reminder_scheduling_is_durable
    CON-004  short-lived, purpose-bound, hashed          test_token_is_hashed_purpose_bound_and_expiring
    CON-004  no LiveKit credential in the email          test_no_media_credential_is_ever_emailed
    CON-004  no false non-forwardable claim              test_forwardability_claim_matches_enforcement
    CON-005  a disconnect is not an ending               test_transient_disconnect_sends_nothing
    ALL      contribution is never marketing consent     test_no_stage_creates_marketing_eligibility

Run with `python test_contributor_access.py` (or pytest).
"""
import ast
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app.email as email_mod
from app.db import SessionLocal
from app.models import (
    NETWORK_TEST_SUPPORTED,
    TECH_CHECK_VALIDITY_POLICY,
    TOKEN_PURPOSE_EVENT_ACCESS,
    TOKEN_PURPOSE_INVITATION,
    ContributorAccessToken,
    ContributorSession,
    ContributorTechnicalCheck,
    Event,
    EventAssignment,
    EventContributorGrant,
    EventLifecycleEvent,
    EventRehearsal,
    Organization,
    User,
)
from app.security import hash_password
from app.services import contributor_access as ca
from app.services import event_planning

PASSWORD = "correct-horse-battery"

INVITE = email_mod.CON_001_INVITE_SUBJECT
ACCEPTED = email_mod.CON_001_ACCEPTED_SUBJECT
REMINDER = email_mod.CON_001_REMINDER_SUBJECT
REVOKED = email_mod.CON_001_REVOKED_SUBJECT
EXPIRED = email_mod.CON_001_EXPIRED_SUBJECT
TECH_REQUIRED = email_mod.CON_002_REQUIRED_SUBJECT
TECH_PASSED = email_mod.CON_002_PASSED_SUBJECT
TECH_ATTENTION = email_mod.CON_002_ATTENTION_SUBJECT
TECH_ALERT = email_mod.CON_002_ALERT_SUBJECT
REHEARSAL = email_mod.CON_003_SUBJECT
ACCESS = email_mod.CON_004_SUBJECT
SESSION_ENDED = email_mod.CON_005_SUBJECT

GOOD_PREFLIGHT = {"browser_supported": True, "camera_ok": True, "mic_ok": True,
                  "speaker_ok": True, "framing_ok": True, "network_quality": "good"}
BAD_PREFLIGHT = {"browser_supported": True, "camera_ok": False, "mic_ok": True,
                 "speaker_ok": False, "framing_ok": False, "network_quality": "poor"}


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


def _new_email(tag="con"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


def _now():
    return datetime.now(timezone.utc)


class World:
    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Con Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata")
            db.add(org)
            db.flush()
            self.org_id = org.id
            self.owner_email = _new_email("owner")
            self.owner_id = self._u(db, "host", self.owner_email, "Event Owner")
            self.admin_email = _new_email("admin")
            self.admin_id = self._u(db, "org_admin", self.admin_email, "Org Admin")
            self.member_email = _new_email("member")
            self.member_id = self._u(db, "viewer", self.member_email, "Internal Member")
            org.owner_user_id = self.admin_id
            # Never given an account: this is the whole point of the family.
            self.external_email = _new_email("guest")

            ev = Event(org_id=org.id, created_by=self.owner_id, title="Con Summit",
                       status="scheduled", timezone="Europe/London",
                       start_time=_now() + timedelta(days=10),
                       end_time=_now() + timedelta(days=10, hours=2),
                       visibility="private", recording_enabled=True)
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

    def user_count(self):
        db = SessionLocal()
        try:
            return db.query(User).count()
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
            grants = db.query(EventContributorGrant).filter(
                EventContributorGrant.event_id == self.event_id).all()
            for g in grants:
                db.query(ContributorAccessToken).filter(
                    ContributorAccessToken.grant_id == g.id).delete()
                db.query(ContributorTechnicalCheck).filter(
                    ContributorTechnicalCheck.grant_id == g.id).delete()
            db.commit()
            db.query(ContributorSession).filter(
                ContributorSession.event_id == self.event_id).delete()
            db.query(EventContributorGrant).filter(
                EventContributorGrant.event_id == self.event_id).delete()
            db.query(EventRehearsal).filter(
                EventRehearsal.event_id == self.event_id).delete()
            db.query(EventLifecycleEvent).filter(
                EventLifecycleEvent.org_id == self.org_id).delete()
            db.query(EventAssignment).filter(
                EventAssignment.event_id == self.event_id).delete()
            db.commit()
            ev = db.get(Event, self.event_id)
            if ev is not None:
                db.delete(ev)
            db.commit()
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


def _invite(db, w, email=None, role="speaker", **kw):
    ev = db.get(Event, w.event_id)
    return ca.invite(db, ev, email=email or w.external_email, role=role,
                     invited_by=w.admin_id, **kw)


# ══ CON-001 ═════════════════════════════════════════════════════════════════════════════

def test_external_contributor_needs_no_membership(w):
    """1, 2, 4, 5, 6 - the core fix."""
    before = w.user_count()
    db = SessionLocal()
    try:
        grant, raw = _invite(db, w)
        assert grant is not None and raw
        assert grant.email == w.external_email.lower()
        assert grant.user_id is None, "an external contributor has no platform account"
        assert grant.role == "speaker" and grant.event_id == w.event_id
        assert grant.status == "invited"
    finally:
        db.close()
    # No user, no membership, no role was created as a side effect.
    assert w.user_count() == before, "inviting a contributor must not create a user"
    db = SessionLocal()
    try:
        assert db.scalar(
            __import__("sqlalchemy").select(User).where(
                User.email == w.external_email.lower())) is None
    finally:
        db.close()


def test_internal_member_can_also_be_a_contributor(w):
    """3 - and the grant resolves their account without changing their membership."""
    db = SessionLocal()
    try:
        member = db.get(User, w.member_id)
        role_before, org_before = member.role, member.org_id
        grant, _raw = _invite(db, w, email=w.member_email, role="panelist")
        assert grant.user_id == w.member_id, "an existing account should be linked"
        db.expire_all()
        member = db.get(User, w.member_id)
        assert member.role == role_before and member.org_id == org_before, (
            "a contributor grant must not alter organization membership")
    finally:
        db.close()


def test_cta_never_reaches_an_organizer_console(w):
    """7, 8 - the launch boundary."""
    db = SessionLocal()
    try:
        grant, raw = _invite(db, w)
        cap, ctx = _capture()
        with ctx:
            assert ca.notify_invited(db, _Bg(), grant, raw) is True
        blob = cap.blob()
        assert f"/contributor/events/{w.event_id}" in blob, blob[:400]
        for forbidden in ("/host/dashboard", "/moderator/dashboard", "/admin",
                          "super_admin", "/organization/settings", "/billing"):
            assert forbidden not in blob, f"contributor CTA must not reach {forbidden}"
    finally:
        db.close()


def test_invitation_states_requirements_and_consent(w):
    """9, 10, 11 - truthfully, from the grant's own flags."""
    db = SessionLocal()
    try:
        grant, raw = _invite(db, w, technical_check_required=True, rehearsal_required=False)
        cap, ctx = _capture()
        with ctx:
            ca.notify_invited(db, _Bg(), grant, raw)
        text = cap.of(INVITE.format(event="Con Summit"))["text"]
        assert "Technical check: Required" in text, text
        assert "Rehearsal: Not required" in text, text
        assert "Europe/London" in text, "the event timezone must be used"
        assert "recorded" in text.lower() and "replay" in text.lower(), "consent disclosure"
        assert "camera, microphone and name are visible" in text, "consent disclosure"
        assert "does not subscribe you to marketing" in text
        assert "Join backstage and appear on air" in text, "role access must be stated"
    finally:
        db.close()


def test_accepted_reminder_revoked_and_expired_variants(w):
    """12, 13, 14, 16, 18 - and each transition is single-shot."""
    db = SessionLocal()
    try:
        grant, raw = _invite(db, w)
        ok, err = ca.accept(db, grant)
        assert ok and grant.status == "accepted" and grant.accepted_at, err
        cap, ctx = _capture()
        with ctx:
            assert ca.notify_accepted(db, _Bg(), grant) is True
            n = len(cap.calls)
            assert ca.notify_accepted(db, _Bg(), grant) is False   # 18 dedup
        assert len(cap.calls) == n
        text = cap.of(ACCEPTED.format(event="Con Summit"))["text"]
        # No live credential at acceptance time.
        assert "sent when the join window opens" in text, text

        cap2, ctx2 = _capture()
        with ctx2:
            assert ca.notify_reminder(db, _Bg(), grant) is True
        assert "Complete your technical check" in cap2.of(
            REMINDER.format(event="Con Summit"))["text"]

        cap3, ctx3 = _capture()
        with ctx3:
            assert ca.revoke(db, grant, actor_id=w.admin_id,
                             reason="Schedule conflict") is True
            assert ca.notify_revoked(db, _Bg(), grant) is True
        assert "Schedule conflict" in cap3.of(REVOKED.format(event="Con Summit"))["text"]
    finally:
        db.close()


def test_expired_invitation_cannot_be_accepted(w):
    """16, 17 - expiry comes from real expires_at and genuinely blocks acceptance."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w, expires_at=_now() - timedelta(hours=1))
        assert ca.is_expired(grant) is True
        ok, err = ca.accept(db, grant)
        assert ok is False and "expired" in (err or "").lower()
        db.expire_all()
        grant = db.get(EventContributorGrant, grant.id)
        assert grant.status == "expired", grant.status
        cap, ctx = _capture()
        with ctx:
            assert ca.notify_expired(db, _Bg(), grant) is True
        assert cap.count(EXPIRED.format(event="Con Summit")) == 1
    finally:
        db.close()


def test_revoke_invalidates_every_access_path(w):
    """15 - revocation is enforcement, not a label."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        grant.access_window_start = _now() - timedelta(minutes=5)
        grant.access_window_end = _now() + timedelta(hours=2)
        db.commit()
        token = ca.issue_token(db, grant, purpose=TOKEN_PURPOSE_EVENT_ACCESS,
                               expires_at=grant.access_window_end)
        found, err = ca.exchange(db, token, purpose=TOKEN_PURPOSE_EVENT_ACCESS)
        assert found is not None, err

        ca.revoke(db, grant, actor_id=w.admin_id)
        found, err = ca.exchange(db, token, purpose=TOKEN_PURPOSE_EVENT_ACCESS)
        assert found is None, "a revoked grant must reject its old link"
        ok, reason = ca.authorize(db, grant)
        assert ok is False and "revoked" in (reason or "").lower()
        assert ca.issue_media_credential(db, grant) is None
    finally:
        db.close()


def test_invitation_html_text_and_provider_failure(w):
    """19, 20."""
    db = SessionLocal()
    try:
        grant, raw = _invite(db, w)
        cap, ctx = _capture()
        with ctx:
            ca.notify_invited(db, _Bg(), grant, raw)
        payload = cap.of(INVITE.format(event="Con Summit"))
        assert payload["html"].strip() and payload["text"].strip()

        grant2, raw2 = _invite(db, w, email=_new_email("g2"))
        capf, ctxf = _capture(fail=True)
        with ctxf:
            ca.notify_invited(db, _Bg(), grant2, raw2)
        db.expire_all()
        assert db.get(EventContributorGrant, grant2.id).status == "invited", (
            "a provider outage must not affect the grant")
    finally:
        db.close()


# ══ CON-002 ═════════════════════════════════════════════════════════════════════════════

def test_technical_check_uses_real_browser_results(w):
    """1, 3, 4, 5, 7 - the result comes from the device, not from the email layer."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        check = ca.technical_check(db, grant)
        assert check is not None and check.status == "required", "acceptance requires a check"

        cap, ctx = _capture()
        with ctx:
            assert ca.notify_technical_check(db, _Bg(), grant, check) == "required"
        text = cap.of(TECH_REQUIRED.format(event="Con Summit"))["text"]
        # 2 - nothing has passed yet.
        assert "passed" not in text.lower().split("what we check")[0], text

        check = ca.apply_preflight(db, grant, GOOD_PREFLIGHT)
        assert check.status == "passed"
        assert check.browser_result is True and check.camera_result is True
        assert check.microphone_result is True
        cap2, ctx2 = _capture()
        with ctx2:
            assert ca.notify_technical_check(db, _Bg(), grant, check) == "passed"
        assert cap2.count(TECH_PASSED.format(event="Con Summit")) == 1
    finally:
        db.close()


def test_needs_attention_uses_safe_categories_only(w):
    """8, 9, 13 - no raw diagnostics, no addresses, no user agents."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        check = ca.apply_preflight(db, grant, BAD_PREFLIGHT)
        assert check.status == "needs_attention"
        assert "Camera unavailable or permission blocked" in (check.failure_categories or [])
        cap, ctx = _capture()
        with ctx:
            ca.notify_technical_check(db, _Bg(), grant, check)
        blob = cap.blob()
        assert "Camera unavailable or permission blocked" in blob
        for raw in ("Mozilla/", "user_agent", "userAgent", "192.168", "deviceId",
                    "sdp", "iceCandidate"):
            assert raw not in blob, f"raw diagnostic leaked: {raw}"
    finally:
        db.close()


def test_network_test_is_not_faked(w):
    """6 - there is no measured network test, so none is claimed."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        assert NETWORK_TEST_SUPPORTED is False
        # A "poor" self-report must not by itself fail the check, because it is not measured.
        check = ca.apply_preflight(db, grant, {**GOOD_PREFLIGHT, "network_quality": "poor"})
        assert check.status == "passed", (
            "an unverified network self-report must not decide the verdict")
        assert check.network_self_report == "poor"
        cap, ctx = _capture()
        with ctx:
            ca.notify_technical_check(db, _Bg(), grant, check)
        # And the copy says plainly that no speed test is run.
        db.expire_all()
        c2 = ca.apply_preflight(db, grant, BAD_PREFLIGHT)
        cap2, ctx2 = _capture()
        with ctx2:
            ca.notify_technical_check(db, _Bg(), grant, c2)
        assert "does not run a network speed test" in cap2.blob()
    finally:
        db.close()


def test_expiry_variant_not_invented(w):
    """10 - no validity policy exists, so no duration is invented."""
    assert TECH_CHECK_VALIDITY_POLICY is None
    assert ca.TECH_CHECK_VALIDITY is None
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        check = ca.apply_preflight(db, grant, GOOD_PREFLIGHT)
        assert check.expires_at is None, (
            "no expiry may be set while no validity policy exists")
    finally:
        db.close()


def test_only_the_assigned_owner_is_alerted(w):
    """11, 12 - a failure does not fan out to the whole event team."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        db.add(EventAssignment(event_id=ev.id, user_id=w.member_id, role="moderator"))
        db.commit()
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        check = ca.apply_preflight(db, grant, BAD_PREFLIGHT)
        cap, ctx = _capture()
        with ctx:
            ca.notify_technical_check(db, _Bg(), grant, check)
        alerted = cap.to(TECH_ALERT.format(event="Con Summit"))
        assert alerted == [w.owner_email.lower()], alerted
        assert w.member_email.lower() not in alerted, (
            "unrelated team members must not be alerted")
        assert w.admin_email.lower() not in alerted
        assert ca.TECHNICIAN_ROLE_SUPPORTED is False
    finally:
        db.close()


def test_technical_check_dedups(w):
    """14."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        check = ca.apply_preflight(db, grant, GOOD_PREFLIGHT)
        cap, ctx = _capture()
        with ctx:
            assert ca.notify_technical_check(db, _Bg(), grant, check) == "passed"
            n = len(cap.calls)
            assert ca.notify_technical_check(db, _Bg(), grant, check) is None
        assert len(cap.calls) == n
    finally:
        db.close()


# ══ CON-003 ═════════════════════════════════════════════════════════════════════════════

def test_rehearsal_reminder_reuses_lve_005(w):
    """1, 7 - one rehearsal model, and the event timezone."""
    import inspect

    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        rehearsal = event_planning.schedule(db, ev, scheduled_at=_now() + timedelta(hours=6),
                                            purpose="Camera and mic check")
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        cap, ctx = _capture()
        with ctx:
            assert ca.notify_rehearsal_reminder(db, _Bg(), grant) is True
        text = cap.of(REHEARSAL.format(event="Con Summit"))["text"]
        assert "Europe/London" in text and "Camera and mic check" in text, text
        # No second rehearsal model exists.
        source = inspect.getsource(ca)
        assert "EventRehearsal" in source
        assert "class " not in source.split("def rehearsal_for")[1][:200]
        assert rehearsal.status == "scheduled"
    finally:
        db.close()


def test_reminder_scheduling_is_durable(w):
    """2, 3 - the marker is a DB column and the sweep runs on the shared leader ticker."""
    import inspect

    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        event_planning.schedule(db, ev, scheduled_at=_now() + timedelta(hours=6))
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        cap, ctx = _capture()
        with ctx:
            assert ca.notify_rehearsal_reminder(db, _Bg(), grant) is True
        # Durable: the fact it was sent survives a brand-new session (a restart).
        db.close()
        db2 = SessionLocal()
        fresh = db2.get(EventContributorGrant, grant.id)
        assert fresh.rehearsal_reminded_version == fresh.rehearsal_reminder_version
        cap2, ctx2 = _capture()
        with ctx2:
            assert ca.notify_rehearsal_reminder(db2, _Bg(), fresh) is False, (
                "a restart must not resend")
        db2.close()
        db = SessionLocal()
        # And it runs on the existing leader-elected ticker, not a CON-specific queue.
        assert "contributor_access" in inspect.getsource(event_planning.sweep)
        assert not hasattr(ca, "run_contributor_sweeper"), (
            "CON must not add its own ticker when shared infrastructure exists")
    finally:
        db.close()


def test_ineligible_states_send_no_reminder(w):
    """4, 5, 6."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        rehearsal = event_planning.schedule(db, ev, scheduled_at=_now() + timedelta(hours=6))
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)

        rehearsal.status = "canceled"
        db.commit()
        ok, why = ca.reminder_eligible(db, grant, ev, ca.rehearsal_for(db, ev))
        assert ok is False, why
        rehearsal.status = "scheduled"
        db.commit()

        ev.status = "cancelled"
        db.commit()
        ok, why = ca.reminder_eligible(db, grant, ev, rehearsal)
        assert ok is False and "event is cancelled" in why
        ev.status = "scheduled"
        db.commit()

        ca.revoke(db, grant, actor_id=w.admin_id)
        ok, why = ca.reminder_eligible(db, grant, ev, rehearsal)
        assert ok is False and "revoked" in why
        cap, ctx = _capture()
        with ctx:
            assert ca.notify_rehearsal_reminder(db, _Bg(), grant) is False
        assert cap.calls == []
    finally:
        db.close()


def test_schedule_change_reschedules_and_never_mails_the_old_time(w):
    """8, 9, 10."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        rehearsal = event_planning.schedule(db, ev, scheduled_at=_now() + timedelta(hours=6))
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        cap, ctx = _capture()
        with ctx:
            assert ca.notify_rehearsal_reminder(db, _Bg(), grant) is True
            first_text = cap.of(REHEARSAL.format(event="Con Summit"))["text"]
            # 10 - a duplicate at the same version is refused.
            assert ca.notify_rehearsal_reminder(db, _Bg(), grant) is False

        moved = _now() + timedelta(hours=8)
        rehearsal.scheduled_at = moved
        db.commit()
        assert ca.invalidate_rehearsal_reminders(db, ev) >= 1
        db.expire_all()
        grant = db.get(EventContributorGrant, grant.id)
        cap2, ctx2 = _capture()
        with ctx2:
            assert ca.notify_rehearsal_reminder(db, _Bg(), grant) is True, (
                "a rescheduled rehearsal must be able to remind again")
        second_text = cap2.of(REHEARSAL.format(event="Con Summit"))["text"]
        assert second_text != first_text
        old_stamp = first_text.split("Rehearsal:")[1].split("\n")[0].strip()
        assert old_stamp not in second_text, "the old time must never be mailed again"
    finally:
        db.close()


# ══ CON-004 ═════════════════════════════════════════════════════════════════════════════

def test_token_is_hashed_purpose_bound_and_expiring(w):
    """1, 2, 3, 4, 5, 6, 9."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        raw = ca.issue_token(db, grant, purpose=TOKEN_PURPOSE_EVENT_ACCESS,
                             expires_at=_now() + timedelta(hours=1))
        # 1 - 256 bits of CSPRNG, url-safe.
        assert len(raw) >= 40, len(raw)
        row = db.scalar(
            __import__("sqlalchemy").select(ContributorAccessToken).where(
                ContributorAccessToken.grant_id == grant.id,
                ContributorAccessToken.purpose == TOKEN_PURPOSE_EVENT_ACCESS))
        # 2 - only the hash is stored.
        assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert raw not in row.token_hash
        for col in ContributorAccessToken.__table__.columns:
            assert "plain" not in col.name
        # 3, 4 - bound to this grant and this event.
        assert row.grant_id == grant.id and row.event_id == w.event_id
        # 5 - purpose binding: an invitation token is not an access token.
        invite_tok = ca.issue_token(db, grant, purpose=TOKEN_PURPOSE_INVITATION)
        found, err = ca.exchange(db, invite_tok, purpose=TOKEN_PURPOSE_EVENT_ACCESS)
        assert found is None, "a token must not work for another purpose"
        # 6, 9 - expiry is enforced.
        row.expires_at = _now() - timedelta(minutes=1)
        db.commit()
        found, err = ca.exchange(db, raw, purpose=TOKEN_PURPOSE_EVENT_ACCESS)
        assert found is None and "expired" in (err or "").lower()
    finally:
        db.close()


def test_access_window_and_wrong_contributor_are_enforced(w):
    """7, 8, 10."""
    db = SessionLocal()
    try:
        # Internal member: the grant HAS a user_id, so identity binding applies.
        grant, _raw = _invite(db, w, email=w.member_email)
        ca.accept(db, grant)
        grant.access_window_start = _now() + timedelta(days=5)
        grant.access_window_end = _now() + timedelta(days=5, hours=3)
        db.commit()
        raw = ca.issue_token(db, grant, purpose=TOKEN_PURPOSE_EVENT_ACCESS,
                             expires_at=grant.access_window_end)
        # 10 - the window is not open yet.
        found, err = ca.exchange(db, raw, purpose=TOKEN_PURPOSE_EVENT_ACCESS)
        assert found is None and "not opened" in (err or "").lower(), err

        grant.access_window_start = _now() - timedelta(minutes=1)
        db.commit()
        # 8 - a different signed-in account is refused.
        found, err = ca.exchange(db, raw, purpose=TOKEN_PURPOSE_EVENT_ACCESS,
                                 signed_in_user_id=w.admin_id)
        assert found is None and "different person" in (err or "").lower(), err
        found, err = ca.exchange(db, raw, purpose=TOKEN_PURPOSE_EVENT_ACCESS,
                                 signed_in_user_id=w.member_id)
        assert found is not None, err
    finally:
        db.close()


def test_no_media_credential_is_ever_emailed(w):
    """11, 12 - the credential is minted server-side, after authorization."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        grant.access_window_start = _now() + timedelta(minutes=30)
        grant.access_window_end = _now() + timedelta(hours=4)
        db.commit()
        cap, ctx = _capture()
        with ctx:
            assert ca.notify_access_ready(db, _Bg(), grant) is True
        blob = cap.blob()
        for secret in ("livekit", "stream_key", "wss://", "eyJhbGci", "api_key",
                       "producer", "LIVEKIT"):
            assert secret.lower() not in blob.lower(), f"{secret} must never be emailed"
        # The link carries a contributor token, which is NOT a media credential.
        assert f"/contributor/events/{w.event_id}/join?t=" in blob
        # 12 - and the media credential only exists after authorization succeeds.
        grant.access_window_start = _now() - timedelta(minutes=1)
        db.commit()
        assert ca.issue_media_credential(db, grant) is not None
        grant.status = "revoked"
        db.commit()
        assert ca.issue_media_credential(db, grant) is None
    finally:
        db.close()


def test_forwardability_claim_matches_enforcement(w):
    """15 - a bearer link is never described as non-forwardable."""
    db = SessionLocal()
    try:
        external, _raw = _invite(db, w)
        ca.accept(db, external)
        assert ca.link_is_identity_bound(external) is False
        external.access_window_start = _now() - timedelta(minutes=1)
        external.access_window_end = _now() + timedelta(hours=3)
        db.commit()
        cap, ctx = _capture()
        with ctx:
            ca.notify_access_ready(db, _Bg(), external)
        blob = cap.blob()
        assert "Keep this link private" in blob
        for false_claim in ("cannot be forwarded", "non-forwardable",
                            "forwarding it gives nobody"):
            assert false_claim not in blob.lower(), (
                f"an unenforceable claim was made to an external contributor: {false_claim}")

        internal, _raw2 = _invite(db, w, email=w.member_email)
        ca.accept(db, internal)
        assert ca.link_is_identity_bound(internal) is True
        internal.access_window_start = _now() - timedelta(minutes=1)
        internal.access_window_end = _now() + timedelta(hours=3)
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            ca.notify_access_ready(db, _Bg(), internal)
        # Only claimed where exchange() genuinely compares identity.
        assert "forwarding it gives nobody else access" in cap2.blob()
    finally:
        db.close()


def test_schedule_change_updates_access_window(w):
    """14 - and kills tokens minted against the old one."""
    db = SessionLocal()
    try:
        ev = db.get(Event, w.event_id)
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        before = grant.access_window_start
        raw = ca.issue_token(db, grant, purpose=TOKEN_PURPOSE_EVENT_ACCESS,
                             expires_at=_now() + timedelta(hours=2))
        ev.start_time = ev.start_time + timedelta(days=3)
        db.commit()
        assert ca.reissue_for_schedule_change(db, ev) >= 1
        db.expire_all()
        grant = db.get(EventContributorGrant, grant.id)
        assert grant.access_window_start != before, "the window must move with the event"
        found, _err = ca.exchange(db, raw, purpose=TOKEN_PURPOSE_EVENT_ACCESS)
        assert found is None, "a token minted for the old schedule must be revoked"
    finally:
        db.close()


def test_contributor_cannot_reach_organization_admin(w):
    """13 - a grant authorizes one event and nothing else."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w)
        ca.accept(db, grant)
        assert grant.user_id is None, "external contributor holds no account at all"
        # Nothing in the service writes membership or role.
        import inspect
        source = inspect.getsource(ca)
        for write in ("User.org_id =", ".org_id = ", "user.role =", ".role = \"org_admin\""):
            assert write not in source, f"contributor_access must not write {write!r}"
        assert ca.backstage_url(w.event_id).endswith(f"/contributor/events/{w.event_id}")
    finally:
        db.close()


# ══ CON-005 ═════════════════════════════════════════════════════════════════════════════

def _session(db, w, grant):
    session = ContributorSession(event_id=w.event_id, org_id=w.org_id,
                                 user_id=w.member_id, identity=str(w.member_id),
                                 state="live", grant_id=grant.id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_transient_disconnect_sends_nothing(w):
    """1 - the critical negative case."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w, email=w.member_email)
        ca.accept(db, grant)
        session = _session(db, w, grant)
        # Exactly what a websocket drop writes today.
        session.last_disconnected_at = _now()
        db.commit()
        cap, ctx = _capture()
        with ctx:
            assert ca.notify_session_ended(db, _Bg(), grant, session) is False, (
                "a disconnect timestamp must not be treated as a session end")
        assert cap.calls == []
        db.expire_all()
        assert db.get(ContributorSession, session.id).ended_at is None
    finally:
        db.close()


def test_authoritative_ends_and_safe_reasons(w):
    """2, 3, 4, 5, 6, 7."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w, email=w.member_email)
        ca.accept(db, grant)
        session = _session(db, w, grant)
        assert ca.end_session(db, session, reason="not_a_reason") is False
        assert ca.end_session(db, session, reason="left") is True
        cap, ctx = _capture()
        with ctx:
            assert ca.notify_session_ended(db, _Bg(), grant, session) is True
            n = len(cap.calls)
            assert ca.notify_session_ended(db, _Bg(), grant, session) is False  # 6
        assert len(cap.calls) == n
        text = cap.of(SESSION_ENDED.format(event="Con Summit"))["text"]
        assert "You left the session" in text, text
        # 3 - the event ending closes an open session.
        grant2, _r2 = _invite(db, w, email=_new_email("g3"))
        ca.accept(db, grant2)
        s2 = ContributorSession(event_id=w.event_id, org_id=w.org_id,
                                user_id=w.owner_id, identity=str(w.owner_id),
                                state="live", grant_id=grant2.id)
        db.add(s2)
        db.commit()
        ev = db.get(Event, w.event_id)
        ev.status = "ended"
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            assert ca.end_sessions_for_event(db, ev, _Bg()) >= 1
        db.expire_all()
        assert db.get(ContributorSession, s2.id).end_reason == "event_ended"
    finally:
        db.close()


def test_revoke_closes_the_session(w):
    """5."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w, email=w.member_email)
        ca.accept(db, grant)
        session = _session(db, w, grant)
        ca.revoke(db, grant, actor_id=w.admin_id)
        db.expire_all()
        session = db.get(ContributorSession, session.id)
        assert session.ended_at is not None
        assert session.end_reason == "access_revoked"
    finally:
        db.close()


def test_no_moderation_detail_in_session_end(w):
    """8."""
    db = SessionLocal()
    try:
        grant, _raw = _invite(db, w, email=w.member_email)
        ca.accept(db, grant)
        session = _session(db, w, grant)
        session.removed_reason = "Removed for abusive conduct - see incident SEC-9"
        db.commit()
        ca.end_session(db, session, reason="access_revoked")
        cap, ctx = _capture()
        with ctx:
            ca.notify_session_ended(db, _Bg(), grant, session)
        blob = cap.blob().lower()
        for leak in ("abusive", "sec-9", "conduct", "incident"):
            assert leak not in blob, f"moderation detail leaked: {leak}"
    finally:
        db.close()


# ══ marketing separation ════════════════════════════════════════════════════════════════

def test_no_stage_creates_marketing_eligibility(w):
    """9, 10 across the whole lifecycle - the critical requirement."""
    from app.services import notifications

    db = SessionLocal()
    try:
        grant, raw = _invite(db, w)
        stages = []
        cap, ctx = _capture()
        with ctx:
            ca.notify_invited(db, _Bg(), grant, raw)
            stages.append("invited")
            ca.accept(db, grant)
            ca.notify_accepted(db, _Bg(), grant)
            stages.append("accepted")
            check = ca.apply_preflight(db, grant, GOOD_PREFLIGHT)
            ca.notify_technical_check(db, _Bg(), grant, check)
            stages.append("joined")
            session = ContributorSession(event_id=w.event_id, org_id=w.org_id,
                                         user_id=w.member_id, identity=str(w.member_id),
                                         state="live", grant_id=grant.id)
            db.add(session)
            db.commit()
            ca.end_session(db, session, reason="left")
            ca.notify_session_ended(db, _Bg(), grant, session)
            stages.append("ended")
        assert stages == ["invited", "accepted", "joined", "ended"]

        # There is no marketing surface in this product, and contributing created none.
        assert notifications.MARKETING_PREFERENCE_KEYS == frozenset()
        from app.db import Base
        for table in Base.metadata.tables:
            for word in ("newsletter", "mailing_list", "audience_list"):
                assert word not in table.lower(), (
                    f"a marketing table appeared ({table}); CON separation must be revisited")
        # There are exactly two subscription lists in the product, and CONTRIBUTING must put
        # nobody on either. `status_subscribers` is a verified opt-in operational channel;
        # `marketing_subscriptions` (ZST-EC-001 MKT) is the only marketing consent record
        # there is. Asserting the contributor's ADDRESS is absent from both is a stronger
        # claim than the old table-name scan, which only proved no such table existed yet.
        from app.models import MARKETING_TOPICS, MarketingSubscription, StatusSubscriber
        from app.services import marketing as mkt

        assert db.query(StatusSubscriber).filter(
            StatusSubscriber.email == grant.email).count() == 0, (
            "contributing must not create a status subscription")
        assert db.query(MarketingSubscription).filter(
            MarketingSubscription.email == grant.email).count() == 0, (
            "contributing must not create marketing consent")
        for topic in MARKETING_TOPICS:
            assert mkt.eligible(db, topic, grant.email) is False, topic
        # The contributor is not on any audience list either.
        from app.models import EventRegistration
        assert db.query(EventRegistration).filter(
            EventRegistration.email == grant.email).count() == 0, (
            "contributing must not create an audience registration")
        # And nothing in the service can reach an audience/marketing sender. Checked against
        # CODE, not prose: the module docstring legitimately discusses marketing separation,
        # so a bare substring search would flag its own explanation.
        import inspect

        tree = ast.parse(inspect.getsource(ca))
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for sender in ("send_viewer_invite_email", "send_registration_confirmation_email",
                       "subscribe", "add_subscriber"):
            assert sender not in called, f"contributor_access calls {sender}"
        # No audience or marketing sender is even imported into reach.
        audience_senders = [n for n in called if "viewer" in n or "registration" in n]
        assert not audience_senders, audience_senders
    finally:
        db.close()


def test_families_classified_and_no_secret_parameters(w):
    from app.services import notifications
    import inspect

    for family in ("CON-001", "CON-002", "CON-003", "CON-004", "CON-005"):
        assert family in notifications.FAMILY_CLASS, family
    for mandatory in ("CON-001", "CON-004", "CON-005"):
        assert notifications.is_mandatory(mandatory) is True, mandatory

    senders = [n for n in dir(email_mod) if n.startswith("send_contributor_")]
    assert len(senders) >= 10, senders
    banned = ("secret", "password", "credential", "jwt", "livekit", "stream_key", "api_key")
    for name in senders:
        for param in inspect.signature(getattr(email_mod, name)).parameters:
            assert not any(b in param.lower() for b in banned), f"{name}({param})"


def test_existing_assignment_behaviour_is_untouched(w):
    """Regression: tenant member assignment still requires membership."""
    from app.crud import event as event_crud

    db = SessionLocal()
    try:
        valid = event_crud.valid_member_ids(db, w.org_id, [w.member_id])
        assert w.member_id in valid, "org members must still validate"
        outsider = uuid.uuid4()
        assert outsider not in event_crud.valid_member_ids(db, w.org_id, [outsider])
        # And the contributor grant path did not weaken it.
        import inspect
        assert "valid_member_ids" not in inspect.getsource(ca), (
            "contributor grants must not depend on the membership gate")
    finally:
        db.close()


TESTS = [
    test_external_contributor_needs_no_membership,
    test_internal_member_can_also_be_a_contributor,
    test_cta_never_reaches_an_organizer_console,
    test_invitation_states_requirements_and_consent,
    test_accepted_reminder_revoked_and_expired_variants,
    test_expired_invitation_cannot_be_accepted,
    test_revoke_invalidates_every_access_path,
    test_invitation_html_text_and_provider_failure,
    test_technical_check_uses_real_browser_results,
    test_needs_attention_uses_safe_categories_only,
    test_network_test_is_not_faked,
    test_expiry_variant_not_invented,
    test_only_the_assigned_owner_is_alerted,
    test_technical_check_dedups,
    test_rehearsal_reminder_reuses_lve_005,
    test_reminder_scheduling_is_durable,
    test_ineligible_states_send_no_reminder,
    test_schedule_change_reschedules_and_never_mails_the_old_time,
    test_token_is_hashed_purpose_bound_and_expiring,
    test_access_window_and_wrong_contributor_are_enforced,
    test_no_media_credential_is_ever_emailed,
    test_forwardability_claim_matches_enforcement,
    test_schedule_change_updates_access_window,
    test_contributor_cannot_reach_organization_admin,
    test_transient_disconnect_sends_nothing,
    test_authoritative_ends_and_safe_reasons,
    test_revoke_closes_the_session,
    test_no_moderation_detail_in_session_end,
    test_no_stage_creates_marketing_eligibility,
    test_families_classified_and_no_secret_parameters,
    test_existing_assignment_behaviour_is_untouched,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
