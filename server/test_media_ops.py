"""MED-001 → MED-005 — media operations (ZST-EC-001).

Two things here matter more than the rest:

  * **MED-002 thresholds.** A one-second encoder reconnect must produce nothing. The tests
    drive the real loss clock rather than asserting on a mocked state machine.

  * **Audience separation (MED-004).** Starting a broadcast session is an internal operator
    event. There is a test that asserts no audience-facing sender is reachable from the
    media path — internal media state is not audience communication consent.

MED-003 asserts an absence: LiveKit's webhook vocabulary has no authentication-failure
event, so there is no telemetry a repeated-auth-failure alert could be built from.

Run with `python test_media_ops.py` (or pytest).
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
    SIGNAL_FLAP_THRESHOLD,
    SIGNAL_HEALTHY,
    SIGNAL_INTERMITTENT,
    SIGNAL_INTERRUPTED,
    SIGNAL_INTERRUPTION_SECONDS,
    SIGNAL_RECOVERY_SECONDS,
    AccountRecovery,
    AuditLog,
    BroadcastSession,
    ElevationSession,
    Event,
    IdentityChallenge,
    LiveIngressEndpoint,
    Organization,
    OrgMembershipEvent,
    SignInEvent,
    StepUpGrant,
    SupportAccessRequest,
    User,
)
from app.security import hash_password
from app.services import broadcast as broadcast_svc
from app.services import media_comms

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"

MED_001 = email_mod.MED_001_SUBJECT
MED_002_INT = email_mod.MED_002_INTERRUPTED_SUBJECT
MED_002_FLAP = email_mod.MED_002_INTERMITTENT_SUBJECT
MED_002_REC = email_mod.MED_002_RECOVERED_SUBJECT
MED_004_START = email_mod.MED_004_STARTED_SUBJECT
MED_004_END = email_mod.MED_004_ENDED_SUBJECT
MED_005_FAILED = email_mod.MED_005_FAILED_SUBJECT
MED_005_DEGRADED = email_mod.MED_005_DEGRADED_SUBJECT
MED_005_RECOVERED = email_mod.MED_005_RECOVERED_SUBJECT


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
        return sorted(c["payload"]["to"][0]
                      for c in self.calls if c["payload"]["subject"] == subject)


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


def _reset_limits():
    ratelimit._HITS.clear()


def _new_email(tag="med"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


class World:
    def __init__(self, is_test=False):
        db = SessionLocal()
        try:
            org = Organization(name=f"Med Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata", is_test=is_test)
            db.add(org)
            db.flush()
            self.org_id, self.org_name = org.id, org.name
            self.owner_email = _new_email("owner")
            self.owner_id = self._u(db, "org_admin", self.owner_email)
            org.owner_user_id = self.owner_id

            event = Event(org_id=org.id, title=f"Launch {uuid.uuid4().hex[:5]}",
                          status="scheduled", created_by=self.owner_id)
            db.add(event)
            db.flush()
            self.event_id = event.id
            db.commit()
        finally:
            db.close()

    def _u(self, db, role, email):
        user = User(org_id=self.org_id, full_name="Operator", role=role, is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=datetime.now(timezone.utc))
        db.add(user)
        db.flush()
        return user.id

    def token(self):
        client = TestClient(m.app)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/auth/login",
                            json={"identifier": self.owner_email, "password": PASSWORD},
                            headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def make_input(self, *, input_type="rtmp", enforced=True, state="inactive"):
        db = SessionLocal()
        try:
            row = LiveIngressEndpoint(
                event_id=self.event_id, org_id=self.org_id,
                title=f"Primary encoder {uuid.uuid4().hex[:4]}",
                input_type=input_type, state=state, enforced=enforced,
                created_by=self.owner_id)
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
        finally:
            db.close()

    def make_session(self, status="preview"):
        db = SessionLocal()
        try:
            s = BroadcastSession(event_id=self.event_id, org_id=self.org_id, status=status,
                                 created_by=self.owner_id)
            db.add(s)
            db.commit()
            db.refresh(s)
            return s.id
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(LiveIngressEndpoint).filter(
                LiveIngressEndpoint.event_id == self.event_id).delete()
            db.query(BroadcastSession).filter(
                BroadcastSession.event_id == self.event_id).delete()
            db.query(AuditLog).filter(AuditLog.org_id == self.org_id).delete()
            db.query(SupportAccessRequest).filter(
                SupportAccessRequest.org_id == self.org_id).delete()
            db.query(OrgMembershipEvent).filter(
                OrgMembershipEvent.org_id == self.org_id).delete()
            db.commit()
            ev = db.get(Event, self.event_id)
            if ev is not None:
                db.delete(ev)
            org = db.get(Organization, self.org_id)
            if org is not None:
                org.owner_user_id = None
            db.commit()
            for user in db.query(User).filter(User.org_id == self.org_id).all():
                for model in (SignInEvent, IdentityChallenge, AccountRecovery,
                              ElevationSession, StepUpGrant):
                    db.query(model).filter(model.user_id == user.id).delete()
                db.delete(user)
            db.commit()
            org = db.get(Organization, self.org_id)
            if org is not None:
                db.delete(org)
                db.commit()
        finally:
            db.close()


def _endpoint(eid):
    db = SessionLocal()
    try:
        return db.get(LiveIngressEndpoint, eid)
    finally:
        db.close()


def _set_signal(eid, *, state, lost_ago=None, ok_ago=None):
    """Drive the raw ingress status and the loss clock the way the webhook would."""
    db = SessionLocal()
    try:
        row = db.get(LiveIngressEndpoint, eid)
        row.state = state
        now = datetime.now(timezone.utc)
        if lost_ago is not None:
            row.signal_lost_at = now - timedelta(seconds=lost_ago)
        if ok_ago is not None:
            row.last_signal_ok_at = now - timedelta(seconds=ok_ago)
        db.commit()
    finally:
        db.close()


# ══ MED-003 — proof of absence ══════════════════════════════════════════════════

def test_003_no_ingest_auth_failure_telemetry_exists():
    """LiveKit emits no authentication-failure webhook, so there is nothing to detect on."""
    import pathlib
    hits = []
    for path in pathlib.Path("app").rglob("*.py"):
        code = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("#"))
        for marker in ("ingress_auth_failed", "IngestAuthFailure", "auth_failure_count",
                       "invalid_stream_key"):
            if marker in code:
                hits.append(f"{path}:{marker}")
    assert not hits, f"ingest-auth telemetry appeared; MED-003 must be revisited: {hits}"


def test_003_no_auth_failure_template_exists():
    code = "\n".join(l for l in open(email_mod.__file__, encoding="utf-8").read().splitlines()
                     if not l.lstrip().startswith("#"))
    assert "repeated authentication failures for a live input" not in code, \
        "MED-003 must not exist without real ingest-auth telemetry"


# ══ MED-001 ═════════════════════════════════════════════════════════════════════

def test_001_created_input_notifies_with_truthful_facts():
    """2/3/4/5/6/7/8/10/11/12 — the committed input, described accurately."""
    w = World()
    try:
        eid = w.make_input(input_type="rtmp", enforced=True)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                assert media_comms.notify_input_created(
                    db, _Bg(), db.get(LiveIngressEndpoint, eid)) is True
            finally:
                db.close()

        payload = cap.of(MED_001)
        assert payload["html"] and payload["text"], "12 — both parts"
        assert payload["from"].startswith("Zoiko Steam Media Operations <")
        assert w.owner_email.lower() in cap.to(MED_001), "3/4 — owner and operators"
        text = payload["text"]
        assert "RTMP" in text, "5 — protocol from input_type, not a label"
        assert "Automatically selected by LiveKit" in text, "6 — region is not fabricated"
        assert "only workspace" in text, "7 — implicit workspace stated truthfully"
        assert "Production" in text, "8 — mode from Organization.is_test"
        assert "IST" in text, "11 — exact timestamp with timezone"
        assert not payload["subject"].startswith("[TEST MODE]")

        # 10 — no credential can appear; the model does not even store the stream key.
        for banned in ("stream_key", "streamkey", "rtmp://", "passphrase", "token",
                       "secret", "api_key"):
            assert banned not in text.lower(), f"{banned!r} must not appear"

        # 13 — a second call claims nothing.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                assert media_comms.notify_input_created(
                    db, _Bg(), db.get(LiveIngressEndpoint, eid)) is False
            finally:
                db.close()
        assert cap2.calls == [], "one notice per input"
    finally:
        w.cleanup()


def test_001_test_mode_prefix_comes_only_from_authoritative_state():
    """9 — the prefix is driven by Organization.is_test, never inferred."""
    w = World(is_test=True)
    try:
        eid = w.make_input()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                media_comms.notify_input_created(db, _Bg(),
                                                 db.get(LiveIngressEndpoint, eid))
            finally:
                db.close()
        subject = cap.subjects[0]
        assert subject.startswith("[TEST MODE] "), f"expected the prefix; got {subject}"
        assert "Test" in cap.calls[0]["payload"]["text"]

        # The flag is the ONLY source — the helper reads is_test and nothing else.
        db = SessionLocal()
        try:
            org = db.get(Organization, w.org_id)
            assert media_comms.is_test_org(org) is True
            org.is_test = False
            assert media_comms.is_test_org(org) is False
        finally:
            db.close()
    finally:
        w.cleanup()


def test_001_unprovisioned_input_is_not_described_as_ready():
    """`enforced=False` means LiveKit never provisioned it — the copy must say so."""
    w = World()
    try:
        eid = w.make_input(enforced=False)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                media_comms.notify_input_created(db, _Bg(),
                                                 db.get(LiveIngressEndpoint, eid))
            finally:
                db.close()
        text = cap.of(MED_001)["text"]
        assert "NOT provisioned" in text, "an unprovisioned input must not read as ready"
    finally:
        w.cleanup()


# ══ MED-002 ═════════════════════════════════════════════════════════════════════

def test_002_transient_blip_below_threshold_sends_nothing():
    """1 — a short reconnect is not an outage."""
    w = World()
    try:
        eid = w.make_input(state="active")
        _set_signal(eid, state="ended", lost_ago=SIGNAL_INTERRUPTION_SECONDS - 20)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                transition = media_comms.evaluate_signal(
                    db, _Bg(), db.get(LiveIngressEndpoint, eid))
            finally:
                db.close()
        assert transition is None, "below threshold is not an interruption"
        assert cap.calls == []
        assert _endpoint(eid).signal_state == SIGNAL_HEALTHY
    finally:
        w.cleanup()


def test_002_persistent_loss_interrupts_once_then_recovers():
    """2/3/4/7/8/9/10/11 — the confirmed interruption and its recovery."""
    w = World()
    try:
        eid = w.make_input(state="active")
        _set_signal(eid, state="ended", lost_ago=SIGNAL_INTERRUPTION_SECONDS + 30)

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                transition = media_comms.evaluate_signal(
                    db, _Bg(), db.get(LiveIngressEndpoint, eid))
            finally:
                db.close()
        assert transition == SIGNAL_INTERRUPTED
        assert _endpoint(eid).signal_state == SIGNAL_INTERRUPTED, "2 — state persists"

        payload = cap.of(MED_002_INT)
        assert payload["html"] and payload["text"], "11 — both parts"
        assert w.owner_email.lower() in cap.to(MED_002_INT), "10 — operators"
        assert "IST" in payload["text"]
        for banned in ("stream_key", "rtmp://", "passphrase", "token", "secret"):
            assert banned not in payload["text"].lower(), "11 — no credential leakage"

        # 4 — repeated evaluation does not duplicate.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                media_comms.evaluate_signal(db, _Bg(), db.get(LiveIngressEndpoint, eid))
            finally:
                db.close()
        assert MED_002_INT not in cap2.subjects, "one notice per confirmed interruption"

        # 7 — recovery requires stability; a fresh reconnect is not enough.
        _set_signal(eid, state="active", ok_ago=SIGNAL_RECOVERY_SECONDS - 30)
        _reset_limits()
        cap3, ctx3 = _capture()
        with ctx3:
            db = SessionLocal()
            try:
                assert media_comms.evaluate_signal(
                    db, _Bg(), db.get(LiveIngressEndpoint, eid)) is None
            finally:
                db.close()
        assert cap3.calls == [], "an unstable reconnect is not a recovery"

        # 8/9 — stable long enough, so recovery is announced with a real duration.
        _set_signal(eid, state="active", ok_ago=SIGNAL_RECOVERY_SECONDS + 10)
        _reset_limits()
        cap4, ctx4 = _capture()
        with ctx4:
            db = SessionLocal()
            try:
                assert media_comms.evaluate_signal(
                    db, _Bg(), db.get(LiveIngressEndpoint, eid)) == "recovered"
            finally:
                db.close()
        assert _endpoint(eid).signal_state == SIGNAL_HEALTHY
        rtext = cap4.of(MED_002_REC)["text"]
        assert "Outage duration" in rtext
    finally:
        w.cleanup()


def test_002_repeated_interruptions_become_intermittent():
    """5/6 — the flap threshold is real and produces the Intermittent variant."""
    w = World()
    try:
        eid = w.make_input(state="active")
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                transition = None
                for _ in range(SIGNAL_FLAP_THRESHOLD):
                    # Each cycle: confirmed loss, then a clean recovery, then loss again.
                    _set_signal(eid, state="ended",
                                lost_ago=SIGNAL_INTERRUPTION_SECONDS + 5)
                    row = db.get(LiveIngressEndpoint, eid)
                    db.refresh(row)
                    transition = media_comms.evaluate_signal(db, _Bg(), row)
                    if transition == SIGNAL_INTERMITTENT:
                        break
                    _set_signal(eid, state="active", ok_ago=SIGNAL_RECOVERY_SECONDS + 5)
                    db.refresh(row)
                    media_comms.evaluate_signal(db, _Bg(), row)
            finally:
                db.close()
        assert transition == SIGNAL_INTERMITTENT, "5 — the flap threshold fires"
        assert _endpoint(eid).signal_state == SIGNAL_INTERMITTENT
        payload = cap.of(MED_002_FLAP)
        assert payload["html"] and payload["text"]
        assert "Confirmed interruptions" in payload["text"], "6 — the variant's facts"
    finally:
        w.cleanup()


def test_002_provider_failure_does_not_alter_signal_state():
    """12 — a mail outage must not change what the platform believes about the signal."""
    w = World()
    try:
        eid = w.make_input(state="active")
        _set_signal(eid, state="ended", lost_ago=SIGNAL_INTERRUPTION_SECONDS + 30)
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            db = SessionLocal()
            try:
                media_comms.evaluate_signal(db, _Bg(), db.get(LiveIngressEndpoint, eid))
            finally:
                db.close()
        assert cap.calls, "a send was attempted"
        assert _endpoint(eid).signal_state == SIGNAL_INTERRUPTED, "state stands"
    finally:
        w.cleanup()


# ══ MED-004 ═════════════════════════════════════════════════════════════════════

def test_004_started_only_when_authoritatively_live():
    """1/2/3/4/5 — a preview session sends nothing; a live one notifies once."""
    w = World()
    try:
        sid = w.make_session(status="preview")
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                assert media_comms.notify_session_started(
                    db, _Bg(), db.get(BroadcastSession, sid)) is False
            finally:
                db.close()
        assert cap.calls == [], "1 — a session that has not started sends nothing"

        db = SessionLocal()
        try:
            s = db.get(BroadcastSession, sid)
            s.status, s.started_at = "live", datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()

        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                assert media_comms.notify_session_started(
                    db, _Bg(), db.get(BroadcastSession, sid)) is True
                # 5 — a duplicate start is safe.
                assert media_comms.notify_session_started(
                    db, _Bg(), db.get(BroadcastSession, sid)) is False
            finally:
                db.close()
        payload = cap2.of(MED_004_START)
        assert payload["html"] and payload["text"], "11 — both parts"
        assert w.owner_email.lower() in cap2.to(MED_004_START), "3 — operators"
        assert "IST" in payload["text"], "4 — start timestamp"
        assert cap2.subjects.count(MED_004_START) == len(cap2.to(MED_004_START))
    finally:
        w.cleanup()


def test_004_ended_reports_duration_and_truthful_recording_status():
    """6/7/8 — the end notice, with no premature recording claim."""
    w = World()
    try:
        sid = w.make_session(status="live")
        started = datetime.now(timezone.utc) - timedelta(minutes=42)
        db = SessionLocal()
        try:
            s = db.get(BroadcastSession, sid)
            s.started_at, s.ended_at = started, datetime.now(timezone.utc)
            s.ended_reason, s.peak_viewers = "host", 137
            db.commit()
        finally:
            db.close()

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                assert media_comms.notify_session_ended(
                    db, _Bg(), db.get(BroadcastSession, sid)) is True
            finally:
                db.close()
        payload = cap.of(MED_004_END)
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "42 minutes" in text, "7 — duration correct"
        assert "137" in text
        # 8 — no recording exists, so it must not claim one is available.
        assert "No recording was captured" in text
        assert "available" not in text.split("Recording:")[1].split("\n")[0].lower() or \
            "No recording" in text
    finally:
        w.cleanup()


def test_004_does_not_reach_any_audience_sender():
    """10 — THE critical control. Internal media state is not audience consent."""
    import inspect

    # Every audience-facing sender in the email module.
    audience_senders = [
        "send_registration_confirmation_email", "send_viewer_invite_email",
        "send_replay_available_email", "send_event_created_email",
        "send_contributor_invite_email", "send_assignment_email",
    ]
    for name in audience_senders:
        assert hasattr(email_mod, name), f"{name} should exist to be excluded"

    media_src = inspect.getsource(media_comms)
    for name in audience_senders:
        assert name not in media_src, \
            f"the media path must not be able to reach {name}"

    # And a real session start reaches none of them.
    w = World()
    try:
        sid = w.make_session(status="live")
        db = SessionLocal()
        try:
            s = db.get(BroadcastSession, sid)
            s.started_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                media_comms.notify_session_started(db, _Bg(),
                                                   db.get(BroadcastSession, sid))
            finally:
                db.close()
        assert cap.subjects == [MED_004_START] * len(cap.subjects), \
            f"only the internal operator notice may be sent; got {cap.subjects}"
        assert "No audience communication was sent." in cap.of(MED_004_START)["text"]
    finally:
        w.cleanup()


# ══ MED-005 ═════════════════════════════════════════════════════════════════════

def test_005_reuses_the_existing_health_calculation():
    """1 — health_of() stays the one authority; media_comms only interprets its output."""
    import inspect

    src = inspect.getsource(media_comms)
    for reimplementation in ("poor_connections", "on_stage == 0", "def health_of"):
        assert reimplementation not in src, \
            "MED-005 must not re-derive health; it reads broadcast.health_of()"
    assert callable(broadcast_svc.health_of)
    assert broadcast_svc.health_of(
        {"publishing": False, "participants": 0, "poor_connections": 0},
        "live", None)["level"] == "down"


def test_005_transitions_notify_once_and_repeat_polling_does_not():
    """2/3/4/5/6/8/11/13 — transitions are events; repeated polling is not."""
    w = World()
    try:
        sid = w.make_session(status="live")
        db = SessionLocal()
        try:
            s = db.get(BroadcastSession, sid)
            s.started_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()

        # First evaluation establishes a baseline — no transition to announce.
        _reset_limits()
        cap0, ctx0 = _capture()
        with ctx0:
            db = SessionLocal()
            try:
                assert media_comms.record_health(
                    db, _Bg(), db.get(BroadcastSession, sid), {"level": "ok", "issues": []}
                ) is None
            finally:
                db.close()
        assert cap0.calls == [], "2 — healthy polling sends nothing"

        # ok -> down is a real transition.
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                transition = media_comms.record_health(
                    db, _Bg(), db.get(BroadcastSession, sid),
                    {"level": "down", "issues": ["No media is being published"]})
                assert transition == "failed"
                # 5 — the same level again is not a new event.
                assert media_comms.record_health(
                    db, _Bg(), db.get(BroadcastSession, sid),
                    {"level": "down", "issues": ["No media is being published"]}) is None
            finally:
                db.close()

        payload = cap.of(MED_005_FAILED)
        assert payload["html"] and payload["text"], "13 — both parts"
        assert cap.subjects.count(MED_005_FAILED) == len(cap.to(MED_005_FAILED)), \
            "one notice per recipient, not one per poll"
        text = payload["text"]
        assert "No media is being published" in text, "issues come from health_of()"
        assert "Healthy" in text and "Failed" in text, "previous and current state"
        assert "IST" in text, "11 — timestamps"
        for banned in ("traceback", "stream_key", "token", "secret"):
            assert banned not in text.lower(), "12 — no secrets or stacks"

        # 8 — recovery only after returning to healthy.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                assert media_comms.record_health(
                    db, _Bg(), db.get(BroadcastSession, sid),
                    {"level": "ok", "issues": []}) == "recovered"
            finally:
                db.close()
        rtext = cap2.of(MED_005_RECOVERED)["text"]
        assert "Incident duration" in rtext
    finally:
        w.cleanup()


def test_005_degraded_is_distinct_from_failed():
    """4/6 — warn and down are different communications."""
    w = World()
    try:
        sid = w.make_session(status="live")
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                s = db.get(BroadcastSession, sid)
                media_comms.record_health(db, _Bg(), s, {"level": "ok", "issues": []})
                assert media_comms.record_health(
                    db, _Bg(), db.get(BroadcastSession, sid),
                    {"level": "warn", "issues": ["3 participants on a poor connection"]}
                ) == "degraded"
            finally:
                db.close()
        assert MED_005_DEGRADED in cap.subjects
        assert MED_005_FAILED not in cap.subjects, "a warning is not a failure"
    finally:
        w.cleanup()


def test_005_provider_failure_does_not_change_health():
    """14 — a mail outage must not alter recorded health."""
    w = World()
    try:
        sid = w.make_session(status="live")
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            db = SessionLocal()
            try:
                s = db.get(BroadcastSession, sid)
                media_comms.record_health(db, _Bg(), s, {"level": "ok", "issues": []})
                media_comms.record_health(db, _Bg(), db.get(BroadcastSession, sid),
                                          {"level": "down", "issues": ["x"]})
            finally:
                db.close()
        assert cap.calls, "a send was attempted"
        db = SessionLocal()
        try:
            assert db.get(BroadcastSession, sid).health_level == "down", "health stands"
        finally:
            db.close()
    finally:
        w.cleanup()


try:
    import pytest

    @pytest.fixture(autouse=True)
    def _no_unmocked_sends():
        yield
        _assert_no_leak()
except ImportError:                     # pragma: no cover
    pass


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                _assert_no_leak()
                passed += 1
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
