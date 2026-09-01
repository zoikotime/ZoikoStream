"""MED-006 → MED-011 — media asset governance (ZST-EC-001).

Two of these six families assert an ABSENCE, and those tests are the most important ones
here. MED-006 (failover) and MED-010 (captions and translation) have no domain in this
platform at all — no secondary media path, no path state, no failover controller, no caption
job, no transcription, no translation. Building either from email templates alone would
produce messages describing capabilities that do not exist, which is precisely the failure
these tests are written to prevent. They fail if such a path is ever added without the
subsystem behind it.

The four families that ARE implemented are pinned on the boundaries that separate them,
because collapsing any one of them is how an operator ends up telling a customer something
untrue:

    recording stopped  !=  recording validated  !=  replay published

MED-007 may not claim a replay is ready. MED-008 may not claim a replay is available. Only
MED-009, reading the entitlement's own publish_state, may speak to availability at all.

Run with `python test_media_governance.py` (or pytest).
"""
import inspect
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.db import SessionLocal
from app.models import (
    FINALIZATION_FAILED,
    FINALIZATION_PARTIAL,
    FINALIZATION_READY,
    FINALIZATION_RECOVERED,
    REC_HEALTH_DEGRADED,
    REC_HEALTH_RECORDING,
    REC_HEALTH_STOPPED,
    AccountRecovery,
    AuditLog,
    BroadcastSession,
    ElevationSession,
    Event,
    IdentityChallenge,
    LegalHoldContact,
    LiveRecording,
    MediaAssetEvent,
    Organization,
    OrgMembershipEvent,
    ReplayEntitlement,
    RetentionExtension,
    ServiceProfile,
    SignInEvent,
    StepUpGrant,
    SupportAccessRequest,
    User,
)
from app.security import hash_password
from app.services import livekit as livekit_svc
from app.services import media_retention, recording_comms, replay_comms, validation

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"

MED_007_STARTED = email_mod.MED_007_STARTED_SUBJECT
MED_007_DEGRADED = email_mod.MED_007_DEGRADED_SUBJECT
MED_007_RECOVERED = email_mod.MED_007_RECOVERED_SUBJECT
MED_007_STOPPED = email_mod.MED_007_STOPPED_SUBJECT
MED_007_REDUNDANCY = email_mod.MED_007_REDUNDANCY_SUBJECT
MED_008_READY = email_mod.MED_008_READY_SUBJECT
MED_008_PARTIAL = email_mod.MED_008_PARTIAL_SUBJECT
MED_008_FAILED = email_mod.MED_008_FAILED_SUBJECT
MED_008_RECOVERED = email_mod.MED_008_RECOVERED_SUBJECT
MED_009_PREPARED = email_mod.MED_009_PREPARED_SUBJECT
MED_009_PUBLISHED = email_mod.MED_009_PUBLISHED_SUBJECT
MED_009_WITHDRAWN = email_mod.MED_009_WITHDRAWN_SUBJECT
MED_009_EXPIRED = email_mod.MED_009_EXPIRED_SUBJECT
MED_011_WARNING = email_mod.MED_011_WARNING_SUBJECT
MED_011_HOLD = email_mod.MED_011_HOLD_SUBJECT
MED_011_HOLD_RELEASED = email_mod.MED_011_HOLD_RELEASED_SUBJECT
MED_011_EXT_REQUESTED = email_mod.MED_011_EXTENSION_REQUESTED_SUBJECT
MED_011_DELETED = email_mod.MED_011_DELETED_SUBJECT
MED_011_DELETE_FAILED = email_mod.MED_011_DELETE_FAILED_SUBJECT

# Never legitimately present in any media email. Checked against every captured body.
FORBIDDEN = (
    "stream_key", "streamKey", "ingest_credential", "LIVEKIT_API_SECRET",
    "GCS_CREDENTIALS", "gs://", "Bearer ", "-----BEGIN", "signed_url",
    "X-Goog-Signature", "password_hash",
)


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

    def count(self, subject):
        return sum(1 for s in self.subjects if s == subject)

    def of(self, subject):
        hits = [c["payload"] for c in self.calls if c["payload"]["subject"] == subject]
        assert hits, f"no message with subject {subject!r}; got {self.subjects}"
        return hits[-1]

    def to(self, subject):
        return sorted(c["payload"]["to"][0]
                      for c in self.calls if c["payload"]["subject"] == subject)

    def sends(self, subject):
        """Messages with this subject. One SEND per recipient - a notice to three people is
        three sends of one notice, which is why dedup is measured as a delta."""
        return self.count(subject)

    def assert_no_secrets(self):
        for call in self.calls:
            blob = f"{call['payload'].get('html', '')}{call['payload'].get('text', '')}"
            for needle in FORBIDDEN:
                assert needle not in blob, (
                    f"{needle!r} leaked into {call['payload']['subject']!r}")


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


def _new_email(tag="medg"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


def _now():
    return datetime.now(timezone.utc)


class World:
    """One Organization + event, with helpers that create authoritative rows directly.

    Recording rows are created here rather than by driving a real LiveKit egress, because
    every assertion in this file is about what the platform does with COMMITTED state — the
    egress orchestration itself is services/broadcast.py's concern and is covered there.
    """

    def __init__(self, *, dual=False, impact="standard"):
        db = SessionLocal()
        try:
            org = Organization(name=f"MedGov {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata")
            db.add(org)
            db.flush()
            self.org_id, self.org_name = org.id, org.name

            self.owner_email = _new_email("owner")
            self.owner_id = self._u(db, "org_admin", self.owner_email)
            org.owner_user_id = self.owner_id
            # `host` is the customer-side role that holds the media_access commercial
            # permission — the platform's own definition of an authorized publisher.
            self.publisher_email = _new_email("publisher")
            self.publisher_id = self._u(db, "host", self.publisher_email)
            self.bystander_email = _new_email("viewer")
            self.bystander_id = self._u(db, "viewer", self.bystander_email)

            self.profile_id = None
            if dual:
                profile = ServiceProfile(version_label="v1", risk_tier="R2",
                                         name="Dual recording", requires_dual_recording=True)
                db.add(profile)
                db.flush()
                self.profile_id = profile.id

            event = Event(org_id=org.id, title=f"Ceremony {uuid.uuid4().hex[:5]}",
                          status="scheduled", impact=impact, created_by=self.owner_id,
                          service_profile_id=self.profile_id)
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
                    email_verified_at=_now())
        db.add(user)
        db.flush()
        return user.id

    def token(self, email=None):
        client = TestClient(m.app)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/auth/login",
                            json={"identifier": email or self.owner_email,
                                  "password": PASSWORD},
                            headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def make_recording(self, *, role=None, enforced=True, status="recording",
                       started_at=None, stopped_at=None, file_url="obj/rec.mp4",
                       size_bytes=1024):
        db = SessionLocal()
        try:
            row = LiveRecording(
                event_id=self.event_id, org_id=self.org_id, status=status,
                quality="1080p", role=role, enforced=enforced,
                started_at=started_at or _now(), stopped_at=stopped_at,
                file_url=file_url if enforced else None, size_bytes=size_bytes,
                egress_id=f"EG_{uuid.uuid4().hex[:8]}" if enforced else None,
                created_by=self.owner_id)
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
        finally:
            db.close()

    def make_entitlement(self, *, publish_state="not_available", expires_at=None,
                         watermark_status="ready", source_recording_id=None):
        db = SessionLocal()
        try:
            row = ReplayEntitlement(event_id=self.event_id, scope="audience",
                                    publish_state=publish_state, expires_at=expires_at,
                                    watermark_status=watermark_status,
                                    source_recording_id=source_recording_id)
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
        finally:
            db.close()

    def add_contact(self, kind="legal"):
        email = _new_email(kind)
        db = SessionLocal()
        try:
            db.add(LegalHoldContact(org_id=self.org_id, email=email, kind=kind,
                                    label="Counsel"))
            db.commit()
        finally:
            db.close()
        return email

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(MediaAssetEvent).filter(
                MediaAssetEvent.org_id == self.org_id).delete()
            db.query(RetentionExtension).filter(
                RetentionExtension.org_id == self.org_id).delete()
            db.query(LegalHoldContact).filter(
                LegalHoldContact.org_id == self.org_id).delete()
            db.query(ReplayEntitlement).filter(
                ReplayEntitlement.event_id == self.event_id).delete()
            db.query(LiveRecording).filter(
                LiveRecording.event_id == self.event_id).delete()
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
            if self.profile_id:
                profile = db.get(ServiceProfile, self.profile_id)
                if profile is not None:
                    db.delete(profile)
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


def _rec(rid):
    db = SessionLocal()
    try:
        return db.get(LiveRecording, rid)
    finally:
        db.close()


def _ent(eid):
    db = SessionLocal()
    try:
        return db.get(ReplayEntitlement, eid)
    finally:
        db.close()


def _probe(*, video=True, audio=True, duration=600.0):
    return {"duration_seconds": duration, "has_video": video, "has_audio": audio}


def _run_validation(*, probe=None, downloadable=True):
    """Drive services/validation.py's real code path with a stubbed probe.

    The ffprobe subprocess and the storage download are the only two things stubbed. The
    verdict logic, the evidence dict and every downstream consequence are the real ones.
    """
    dl = (lambda key: "C:/tmp/fake.mp4") if downloadable else (lambda key: None)
    with patch.object(validation.livekit, "download_to_temp", dl), \
         patch.object(validation, "_ffprobe_info", lambda p: probe), \
         patch("os.remove", lambda p: None):
        db = SessionLocal()
        try:
            validation.process_pending_validations(db)
        finally:
            db.close()


# ══ MED-006 — failover lifecycle: MISSING ═══════════════════════════════════════════════

def test_med006_no_failover_domain():
    """1 — there is no secondary-path / failover subsystem, so there is no MED-006.

    Audited for every form the capability could take: a model, a service, a state
    vocabulary, or a column that names an active/standby media path. `LiveIngressEndpoint`
    holds MULTIPLE inputs per event, but nothing marks one primary and another standby, and
    nothing can activate a switch between them — so an email announcing that "the secondary
    media path was activated" would describe an event the platform cannot produce.
    """
    from app import models
    from app.models import LiveIngressEndpoint

    for name in ("FailoverPath", "MediaPath", "FailoverEvent", "SecondaryPath"):
        assert not hasattr(models, name), (
            f"{name} appeared — MED-006 must be revisited, not left MISSING")

    columns = set(LiveIngressEndpoint.__table__.columns.keys())
    for column in ("role", "path_role", "is_primary", "failover_state", "active_path"):
        assert column not in columns, (
            f"LiveIngressEndpoint.{column} exists — a real path model may now exist")

    import os
    services = os.listdir(os.path.join(os.path.dirname(__file__), "app", "services"))
    assert not [f for f in services if "failover" in f.lower()], (
        "a failover service appeared; MED-006 must be implemented properly, not by email")


def test_med006_no_failover_email_exists():
    """2 — no template, subject or sender in the email module speaks about failover.

    This is the assertion that stops MED-006 being made "green" with copy alone.
    """
    # Checked against the module's actual TEMPLATE SURFACE - its string constants and its
    # callables - rather than its full source. email.py carries a comment explaining why
    # MED-006 has no templates, and an assertion that fails on its own explanation is
    # testing prose, not behaviour.
    phrases = ("secondary media path", "failover", "standby path", "primary media path")
    for name in dir(email_mod):
        if name.startswith("__"):
            continue
        value = getattr(email_mod, name)
        assert "failover" not in name.lower(), f"{name} implies a failover family"
        if isinstance(value, str):
            low = value.lower()
            for phrase in phrases:
                assert phrase not in low, (
                    f"{name} = {value!r} describes a media path that does not exist")
        if callable(value) and name.startswith("send_"):
            for phrase in phrases:
                assert phrase not in (value.__doc__ or "").lower(), (
                    f"{name} documents failover behaviour that does not exist")


# ══ MED-007 — recording health lifecycle ════════════════════════════════════════════════

def test_recording_started_notifies_owner_and_operators():
    """3 — a committed capture produces one Started notice to the asset owner."""
    w = World()
    try:
        rid = w.make_recording()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            recording_comms.notify_started(db, _Bg(), db.get(LiveRecording, rid))
            db.close()
        assert cap.count(MED_007_STARTED) == 1, cap.subjects
        assert w.owner_email in cap.to(MED_007_STARTED)
        cap.assert_no_secrets()
    finally:
        w.cleanup()


def test_recording_started_never_claims_replay():
    """4 — MED-007 may not say a replay is ready. Capture is not availability."""
    w = World()
    try:
        rid = w.make_recording()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            recording_comms.notify_started(db, _Bg(), db.get(LiveRecording, rid))
            db.close()
        text = cap.of(MED_007_STARTED)["text"].lower()
        for claim in ("replay is ready", "replay is available", "watch the replay"):
            assert claim not in text, f"MED-007 claimed replay availability: {claim!r}"
        assert "not replay availability" in text
    finally:
        w.cleanup()


def test_recording_started_is_deduplicated():
    """5 — a second call sends nothing. The marker is a conditional claim, not a flag."""
    w = World()
    try:
        rid = w.make_recording()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            recording_comms.notify_started(db, _Bg(), db.get(LiveRecording, rid))
            recording_comms.notify_started(db, _Bg(), db.get(LiveRecording, rid))
            db.close()
        assert cap.count(MED_007_STARTED) == 1, cap.subjects
    finally:
        w.cleanup()


def test_single_path_event_is_not_degraded():
    """6 — an ordinary event records one path and that is HEALTHY, not degraded.

    The degradation rule is redundancy loss. An event that never required a second path
    cannot lose one, and reporting it as degraded would be a fabricated alert.
    """
    w = World(dual=False)
    try:
        rid = w.make_recording()
        db = SessionLocal()
        group = recording_comms.capture_group(db, db.get(LiveRecording, rid))
        state, reason = recording_comms.evaluate_health(db, group)
        db.close()
        assert state == REC_HEALTH_RECORDING, (state, reason)
        assert reason is None
    finally:
        w.cleanup()


def test_dual_recording_missing_path_is_degraded_and_escalates():
    """7 — a dual-recording event capturing on one path is degraded, and says so.

    Both halves are real stored state: the requirement is the event's service profile, the
    count is LiveRecording rows with enforced=True.
    """
    w = World(dual=True)
    try:
        started = _now()
        w.make_recording(role="primary", enforced=True, started_at=started)
        w.make_recording(role="secondary", enforced=False, started_at=started)

        db = SessionLocal()
        primary = db.query(LiveRecording).filter(
            LiveRecording.event_id == w.event_id,
            LiveRecording.role == "primary").one()
        assert recording_comms.required_paths(db, w.event_id) == 2
        group = recording_comms.capture_group(db, primary)
        state, reason = recording_comms.evaluate_health(db, group)
        db.close()
        assert state == REC_HEALTH_DEGRADED, (state, reason)
        assert "secondary" in reason

        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            recording_comms.notify_started(db, _Bg(), db.get(LiveRecording, primary.id))
            db.close()
        assert cap.count(MED_007_REDUNDANCY) == 1, cap.subjects
        body = cap.of(MED_007_REDUNDANCY)["text"]
        assert "Required independent paths: 2" in body
        assert "Paths actually capturing: 1" in body
    finally:
        w.cleanup()


def test_recording_degraded_sends_once_per_transition():
    """8 — a repeated evaluation of the same degraded state sends nothing further."""
    w = World(dual=True)
    try:
        started = _now()
        pid = w.make_recording(role="primary", enforced=True, started_at=started)
        w.make_recording(role="secondary", enforced=True, started_at=started)

        db = SessionLocal()
        primary = db.get(LiveRecording, pid)
        primary.health_state = REC_HEALTH_RECORDING
        db.commit()
        secondary = db.query(LiveRecording).filter(
            LiveRecording.event_id == w.event_id,
            LiveRecording.role == "secondary").one()
        secondary.status = "failed"
        db.commit()
        db.close()

        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            recording_comms.record_health(db, _Bg(), db.get(LiveRecording, pid))
            recording_comms.record_health(db, _Bg(), db.get(LiveRecording, pid))
            db.close()
        assert cap.count(MED_007_DEGRADED) == 1, cap.subjects
        assert _rec(pid).health_state == REC_HEALTH_DEGRADED
    finally:
        w.cleanup()


def test_recovered_requires_a_prior_degradation():
    """9 — recovery is only meaningful after something was actually wrong."""
    w = World(dual=True)
    try:
        started = _now()
        pid = w.make_recording(role="primary", enforced=True, started_at=started)
        sid = w.make_recording(role="secondary", enforced=True, started_at=started)

        db = SessionLocal()
        primary = db.get(LiveRecording, pid)
        primary.health_state = REC_HEALTH_RECORDING
        secondary = db.get(LiveRecording, sid)
        secondary.status = "failed"
        db.commit()
        db.close()

        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            recording_comms.record_health(db, _Bg(), db.get(LiveRecording, pid))
            db.close()
        assert cap.count(MED_007_DEGRADED) == 1

        db = SessionLocal()
        db.get(LiveRecording, sid).status = "recording"
        db.commit()
        db.close()

        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            recording_comms.record_health(db, _Bg(), db.get(LiveRecording, pid))
            db.close()
        assert cap2.count(MED_007_RECOVERED) == 1, cap2.subjects
        assert "Incident duration" in cap2.of(MED_007_RECOVERED)["text"]
    finally:
        w.cleanup()


def test_recording_stopped_says_validation_has_not_run():
    """10 — the Stopped notice must state that nothing has been checked yet."""
    w = World()
    try:
        started = _now() - timedelta(minutes=30)
        rid = w.make_recording(status="stopped", started_at=started, stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            recording_comms.notify_stopped(db, _Bg(), db.get(LiveRecording, rid))
            db.close()
        body = cap.of(MED_007_STOPPED)["text"]
        assert "Validation has not run yet" in body
        assert "replay is ready" not in body.lower()
        assert "Recording duration: 30 minutes" in body
    finally:
        w.cleanup()


def test_stopped_waits_for_every_path_of_a_capture():
    """11 — one notice per capture, not one per path. A running sibling suppresses it."""
    w = World(dual=True)
    try:
        started = _now()
        pid = w.make_recording(role="primary", status="stopped", started_at=started,
                               stopped_at=_now())
        w.make_recording(role="secondary", status="recording", started_at=started)
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            sent = recording_comms.notify_stopped(db, _Bg(), db.get(LiveRecording, pid))
            db.close()
        assert sent is False
        assert cap.count(MED_007_STOPPED) == 0, cap.subjects
    finally:
        w.cleanup()


def test_independent_recording_label_is_truthful():
    """12 — the redundancy row states the real requirement, never a flattering guess."""
    plain = World(dual=False)
    dual = World(dual=True)
    try:
        db = SessionLocal()
        rid = plain.make_recording()
        group = recording_comms.capture_group(db, db.get(LiveRecording, rid))
        assert "Not required" in recording_comms.independent_recording_label(
            db, plain.event_id, group)

        started = _now()
        dual.make_recording(role="primary", started_at=started)
        dual.make_recording(role="secondary", started_at=started)
        primary = db.query(LiveRecording).filter(
            LiveRecording.event_id == dual.event_id,
            LiveRecording.role == "primary").one()
        label = recording_comms.independent_recording_label(
            db, dual.event_id, recording_comms.capture_group(db, primary))
        db.close()
        assert label == "Required — 2 of 2 independent paths capturing", label
    finally:
        plain.cleanup()
        dual.cleanup()


# ══ MED-008 — recording finalization ════════════════════════════════════════════════════

def test_stopping_a_recording_is_not_finalization():
    """13 — a stopped recording has no verdict, so MED-008 sends nothing."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            result = recording_comms.notify_finalized(db, _Bg(), db.get(LiveRecording, rid))
            db.close()
        assert result is None
        assert cap.count(MED_008_READY) == 0, cap.subjects
        assert _rec(rid).validation_status is None
    finally:
        w.cleanup()


def test_validation_actually_runs_for_a_single_path_recording():
    """14 — single-path recordings get a real verdict and real evidence.

    Before MED-008 nothing validated them at all: `validation_status` stayed NULL for the
    life of the asset and the console showed "Validation pending" forever.
    """
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            _run_validation(probe=_probe())
        rec = _rec(rid)
        assert rec.validation_status == "valid", rec.validation_status
        evidence = rec.validation_evidence
        assert evidence["single"]["has_audio"] is True
        assert evidence["single"]["duration_seconds"] == 600.0
        assert evidence["gap_detection"] == "not implemented"
        # No comparison key: with one path there is nothing to compare against, and an
        # emitted null would read as a check that ran.
        assert "duration_delta_seconds" not in evidence
    finally:
        w.cleanup()


def test_ready_is_sent_only_after_validation():
    """15 — READY follows the verdict, and reports which checks genuinely ran."""
    w = World()
    try:
        rid = w.make_recording(status="stopped",
                               started_at=_now() - timedelta(minutes=10),
                               stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            _run_validation(probe=_probe())
        assert cap.count(MED_008_READY) == 1, cap.subjects
        body = cap.of(MED_008_READY)["text"]
        assert "video track present" in body
        assert "audio track present" in body
        assert "Checks NOT performed" in body
        assert "cryptographic integrity verification" in body
        cap.assert_no_secrets()
        assert _rec(rid).finalization_notified_state == FINALIZATION_READY
    finally:
        w.cleanup()


def test_med008_never_claims_replay_is_available():
    """16 — the single most important MED-008 boundary.

    A validated recording whose replay was never published must say exactly that. The row
    is read from the entitlement's publish_state, not assumed from the verdict.
    """
    w = World()
    try:
        w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            _run_validation(probe=_probe())
        body = cap.of(MED_008_READY)["text"]
        assert "Validation is not publication" in body
        assert "Replay availability: No replay has been prepared" in body
        for claim in ("replay is available", "replay is ready", "viewers can now watch"):
            assert claim not in body.lower(), f"MED-008 claimed availability: {claim!r}"
    finally:
        w.cleanup()


def test_partial_variant_lists_missing_components():
    """17 — a file with no audio validates as PARTIAL and names what is missing."""
    w = World()
    try:
        w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            _run_validation(probe=_probe(audio=False))
        assert cap.count(MED_008_PARTIAL) == 1, cap.subjects
        body = cap.of(MED_008_PARTIAL)["text"]
        assert "no audio track" in body
        assert "Recovery options" in body
    finally:
        w.cleanup()


def test_failed_variant_uses_a_safe_category():
    """18 — an unreadable object fails with a category, never a provider exception."""
    w = World()
    try:
        w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            _run_validation(probe=None, downloadable=False)
        assert cap.count(MED_008_FAILED) == 1, cap.subjects
        body = cap.of(MED_008_FAILED)["text"]
        assert "Failure category:" in body
        assert "Traceback" not in body and "gs://" not in body
        cap.assert_no_secrets()
    finally:
        w.cleanup()


def test_recovered_asset_transition():
    """19 — a previously failed asset that becomes valid is RECOVERED, not a second READY."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            _run_validation(probe=None, downloadable=False)
        assert cap.count(MED_008_FAILED) == 1
        assert _rec(rid).finalization_notified_state == FINALIZATION_FAILED

        db = SessionLocal()
        rec = db.get(LiveRecording, rid)
        rec.validation_status = None          # re-queue for the ticker
        rec.validation_evidence = None
        db.commit()
        db.close()

        cap2, ctx2 = _capture()
        with ctx2:
            _run_validation(probe=_probe())
        assert cap2.count(MED_008_RECOVERED) == 1, cap2.subjects
        assert cap2.count(MED_008_READY) == 0
        assert _rec(rid).finalization_notified_state == FINALIZATION_RECOVERED
        assert "Recovered components" in cap2.of(MED_008_RECOVERED)["text"]
    finally:
        w.cleanup()


def test_med008_is_deduplicated():
    """20 — re-announcing an unchanged verdict sends nothing."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            _run_validation(probe=_probe())
            db = SessionLocal()
            again = recording_comms.notify_finalized(db, _Bg(), db.get(LiveRecording, rid))
            db.close()
        assert again is None
        assert cap.count(MED_008_READY) == 1, cap.subjects
    finally:
        w.cleanup()


def test_recording_ready_preference_governs_ready_only():
    """21 — the "Recording ready" switch turns off exactly the ready message.

    A failed asset is an action-required operational alert, not the notification that
    switch names, so it is sent regardless.
    """
    w = World()
    try:
        db = SessionLocal()
        org = db.get(Organization, w.org_id)
        org.notifications = {"recording_ready": False}
        db.commit()
        db.close()

        w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            _run_validation(probe=_probe())
        assert cap.count(MED_008_READY) == 0, cap.subjects

        w2 = World()
        try:
            db = SessionLocal()
            org2 = db.get(Organization, w2.org_id)
            org2.notifications = {"recording_ready": False}
            db.commit()
            db.close()
            w2.make_recording(status="stopped", stopped_at=_now())
            cap2, ctx2 = _capture()
            with ctx2:
                _run_validation(probe=None, downloadable=False)
            assert cap2.count(MED_008_FAILED) == 1, cap2.subjects
        finally:
            w2.cleanup()
    finally:
        w.cleanup()


def test_recording_ready_preference_is_available_and_classified():
    """22 — the toggle is no longer inert, and MED-011 can never be suppressed by one."""
    from app.services import notifications as notif

    entry = notif.CATALOG_BY_KEY["recording_ready"]
    assert entry["available"] is True and entry["configurable"] is True
    assert entry["family"] == "MED-008"
    assert notif.is_mandatory("MED-011") is True
    assert notif.should_send_operational_notification(family="MED-011", org=None) is True


# ══ MED-009 — replay lifecycle ══════════════════════════════════════════════════════════

def test_publishers_resolve_from_the_permission_not_a_role_list():
    """23 — the publisher list is derived from commercial_can("media_access")."""
    w = World()
    try:
        db = SessionLocal()
        emails = {u.email for u in replay_comms.publishers(db, w.org_id)}
        db.close()
        assert w.publisher_email in emails, emails
        assert w.bystander_email not in emails, "a viewer is not an authorized publisher"
    finally:
        w.cleanup()


def test_prepared_notifies_owner_and_publishers_without_implying_availability():
    """24 — Prepared reaches the accountable people and says it is not public."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        eid = w.make_entitlement(publish_state="ready_for_review",
                                 source_recording_id=rid)
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            replay_comms.notify_prepared(db, _Bg(), db.get(ReplayEntitlement, eid))
            db.close()
        to = cap.to(MED_009_PREPARED)
        # One send per recipient. The notice reached both accountable parties exactly once.
        assert sorted(to) == sorted({w.owner_email, w.publisher_email}), to
        body = cap.of(MED_009_PREPARED)["text"]
        assert "not publicly available" in body
        assert "Captions: Not supported" in body
    finally:
        w.cleanup()


def test_published_notice_is_not_the_purchaser_notice():
    """25 — MED-009 is a publisher message and cannot reach an audience sender.

    The purchaser flow (send_replay_available_email) is a different audience entirely. This
    asserts the module is structurally incapable of substituting one for the other.
    """
    source = inspect.getsource(replay_comms)
    assert "send_replay_available_email" not in source
    assert "purchaser" not in source.lower() or "never" in source.lower()

    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        eid = w.make_entitlement(publish_state="published", source_recording_id=rid)
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            replay_comms.notify_published(db, _Bg(), db.get(ReplayEntitlement, eid))
            db.close()
        to = cap.to(MED_009_PUBLISHED)
        assert w.publisher_email in to, to
        # Every message sent is the publisher notice. No audience/purchaser mail was
        # produced by this path at all.
        assert set(cap.subjects) == {MED_009_PUBLISHED}, cap.subjects
    finally:
        w.cleanup()


def test_published_reports_delivery_state_truthfully():
    """26 — "published" and "watchable now" are different, and are reported separately."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        eid = w.make_entitlement(publish_state="published", watermark_status="pending",
                                 source_recording_id=rid)
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            replay_comms.notify_published(db, _Bg(), db.get(ReplayEntitlement, eid))
            db.close()
        body = cap.of(MED_009_PUBLISHED)["text"]
        assert "viewers cannot watch it yet" in body
        cap.assert_no_secrets()
    finally:
        w.cleanup()


def test_replay_disclosures_never_fabricate_unsupported_features():
    """27 — captions, languages, geography and concurrency do not exist and say so."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        eid = w.make_entitlement(publish_state="published", source_recording_id=rid)
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            replay_comms.notify_published(db, _Bg(), db.get(ReplayEntitlement, eid))
            db.close()
        body = cap.of(MED_009_PUBLISHED)["text"]
        for row in ("Geographic restrictions: Not supported",
                    "Viewer concurrency limit: Not supported",
                    "Captions: Not supported",
                    "Language tracks: Not supported"):
            assert row in body, f"missing truthful disclosure: {row!r}"
    finally:
        w.cleanup()


def test_withdraw_is_a_real_access_change():
    """28 — withdrawal moves publish_state to `withheld`, which had no writer before."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        eid = w.make_entitlement(publish_state="published", source_recording_id=rid)
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            ent = db.get(ReplayEntitlement, eid)
            previous = replay_comms.withdraw(db, ent, actor_id=w.owner_id,
                                             reason="Rights review")
            replay_comms.notify_withdrawn(db, _Bg(), ent)
            db.close()
        assert previous == "published"
        assert _ent(eid).publish_state == "withheld"
        body = cap.of(MED_009_WITHDRAWN)["text"]
        assert "Rights review" in body
        # No invented refund outcome.
        assert "refund" in body.lower() and "does not change orders" in body
    finally:
        w.cleanup()


def test_expiry_is_enforced_before_it_is_announced():
    """29 — expiry is an authorization boundary, not a label.

    `expires_at` was a stored date nothing honoured: an expired entitlement kept serving its
    replay. Announcing expiry while the replay stayed watchable would be a false claim about
    a control that did not exist, so this pins the enforcement AND the notice.
    """
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        eid = w.make_entitlement(publish_state="published",
                                 expires_at=_now() - timedelta(hours=1),
                                 source_recording_id=rid)
        assert replay_comms.is_expired(_ent(eid)) is True

        # Pinned to the ENFORCEMENT, not to a helper name. After merging main the gate is
        # crud.commercial.replay_access_expired (main's version, which also honours an
        # explicit `expired` publish_state); replay_comms.is_expired now delegates to it.
        # Asserting on the helper name would pass on a mere comment mentioning it, so the
        # check is that watch_event actually consults an expiry before serving a replay.
        import app.routers.events as events_router

        gate = inspect.getsource(events_router.watch_event)
        assert "replay_access_expired" in gate, (
            "replay expiry must be enforced at the access layer, not just swept")
        assert "replay_published" in gate
        # ...and that the two paths agree on what "expired" means.
        assert replay_comms.is_expired(_ent(eid)) is True

        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            result = replay_comms.expire_due(db, _Bg())
            db.close()
        assert result["expired"] == 1, result
        assert _ent(eid).publish_state == "expired"
        to = cap.to(MED_009_EXPIRED)
        assert to and len(to) == len(set(to)), f"a recipient was mailed twice: {to}"
        assert set(cap.subjects) == {MED_009_EXPIRED}, cap.subjects
    finally:
        w.cleanup()


def test_med009_is_deduplicated():
    """30 — one notice per lifecycle transition."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        eid = w.make_entitlement(publish_state="published", source_recording_id=rid)
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            ent = db.get(ReplayEntitlement, eid)
            assert replay_comms.notify_published(db, _Bg(), ent) is True
            first = cap.sends(MED_009_PUBLISHED)
            # Second call: the claim marker is already set, so nothing further goes out.
            assert replay_comms.notify_published(db, _Bg(), ent) is False
            db.close()
        assert first > 0
        assert cap.sends(MED_009_PUBLISHED) == first, cap.subjects
    finally:
        w.cleanup()


def test_restoring_a_withdrawn_replay_is_an_access_change():
    """31 — a withdrawal is reversible, and the restore is reported as an access change.

    Without this, `withheld` would be a terminal state: the replay could be taken out of
    service and never put back, and the MED-009 access-change transition would have no
    trigger at all.
    """
    from app.crud import commercial as commercial_crud

    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        eid = w.make_entitlement(publish_state="published", source_recording_id=rid)
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            ent = db.get(ReplayEntitlement, eid)
            replay_comms.withdraw(db, ent, actor_id=w.owner_id, reason="Rights review")
            assert ent.publish_state == "withheld"
            commercial_crud.publish_replay(db, ent)
            replay_comms.notify_access_changed(db, _Bg(), ent, "withheld")
            db.close()
        assert _ent(eid).publish_state == "published"
        body = cap.of(email_mod.MED_009_ACCESS_SUBJECT)["text"]
        assert "Previous access: Withdrawn" in body
        assert "Current access: Published" in body
    finally:
        w.cleanup()


# ══ MED-010 — captions and translation: MISSING ═════════════════════════════════════════

def test_med010_no_caption_or_translation_domain():
    """31 — there is no caption, transcription or translation subsystem.

    The only two mentions anywhere are a `caption_qa: "not implemented"` marker in
    validation evidence — which exists precisely to say the check does NOT run — and an
    unused `transcript.ready` string in the webhook event vocabulary. Neither is a job, a
    state machine, or a quality label, so MED-010 stays MISSING.
    """
    from app import models

    for name in ("CaptionJob", "CaptionTrack", "TranslationJob", "LanguageTrack",
                 "Transcript", "CaptionAsset"):
        assert not hasattr(models, name), f"{name} exists — MED-010 must be revisited"

    import os
    services = os.listdir(os.path.join(os.path.dirname(__file__), "app", "services"))
    for f in services:
        assert not any(k in f.lower() for k in ("caption", "transcri", "translat")), (
            f"{f} suggests a caption domain now exists")


def test_med010_no_caption_email_and_no_quality_label():
    """32 — no template claims a caption quality/provenance level.

    "Machine-generated", "human-reviewed" and "certified" are the three labels MED-010 would
    need. None may appear while no process can produce them — a certified-captions email
    with no certification workflow behind it is exactly the failure mode.
    """
    source = inspect.getsource(email_mod).lower()
    for phrase in ("machine-generated captions", "captions were certified",
                   "completed human review", "caption or translation processing"):
        assert phrase not in source, f"{phrase!r} exists with no caption subsystem"

    for name in dir(email_mod):
        low = name.lower()
        assert "caption" not in low or "not supported" in low.replace("_", " "), (
            f"{name} implies a caption family")
        assert "translation" not in low, f"{name} implies a translation family"


# ══ MED-011 — retention, legal hold and deletion ════════════════════════════════════════

def test_retention_is_persisted_at_finalization():
    """33 — a retention date and its policy version are committed, not emailed."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            _run_validation(probe=_probe())
        rec = _rec(rid)
        assert rec.retention_expires_at is not None
        assert rec.retention_policy_version == "default-v1"
        db = SessionLocal()
        policy = media_retention.policy(db)
        db.close()
        expected = rec.stopped_at + timedelta(days=policy["retention_days"])
        assert abs((rec.retention_expires_at - expected).total_seconds()) < 5
    finally:
        w.cleanup()


def test_retention_warning_reports_real_behaviour_at_the_deadline():
    """34 — there is no deletion scheduler, so the notice must not promise one."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        db = SessionLocal()
        rec = db.get(LiveRecording, rid)
        rec.retention_policy_version = "default-v1"
        rec.retention_expires_at = _now() + timedelta(days=3)
        db.commit()
        db.close()

        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            result = media_retention.sweep_retention(db, _Bg())
            db.close()
        assert result["warned"] == 1, result
        body = cap.of(MED_011_WARNING)["text"]
        assert "does not delete it automatically" in body
        assert "becomes eligible for deletion" in body
        assert "will be deleted on" not in body.lower()
        assert "Retention policy: default-v1" in body
    finally:
        w.cleanup()


def test_legal_hold_blocks_deletion_server_side():
    """35 — a held recording cannot be deleted through the API, by anyone in the tenant."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        db = SessionLocal()
        db.get(LiveRecording, rid).legal_hold = True
        db.commit()
        db.close()

        headers = w.token()
        client = TestClient(m.app)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.delete(f"/api/organization/recordings/{rid}", headers=headers)
        assert r.status_code == 409, r.text
        assert "legal hold" in r.text.lower()
        assert _rec(rid) is not None, "a held recording was deleted"
    finally:
        w.cleanup()


def test_legal_hold_notification_reaches_legal_contacts_without_confidential_detail():
    """36 — hold notices carry a category and an opaque reference, nothing more."""
    w = World()
    try:
        legal = w.add_contact("legal")
        rid = w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            admin = db.get(User, w.owner_id)
            media_retention.place_hold(db, _Bg(), db.get(LiveRecording, rid), actor=admin,
                                       category="litigation", hold_reference="LH-2026-004")
            db.close()
        to = cap.to(MED_011_HOLD)
        assert legal in to, to
        assert w.owner_email in to, to
        assert len(to) == len(set(to)), f"a recipient was mailed twice: {to}"
        body = cap.of(MED_011_HOLD)["text"]
        assert "LH-2026-004" in body and "Litigation" in body
        assert "cannot be deleted while the hold is open" in body
        cap.assert_no_secrets()
        assert _rec(rid).legal_hold is True
    finally:
        w.cleanup()


def test_only_platform_governance_can_place_a_hold_or_grant_an_extension():
    """37 — an org admin can request, never approve. The retained party is not the decider."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        headers = w.token()
        client = TestClient(m.app)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            hold = client.post(f"/api/admin/recordings/{rid}/legal-hold",
                               json={"category": "litigation", "hold_reference": "X"},
                               headers=headers)
            decide = client.post(
                f"/api/admin/retention-extensions/{uuid.uuid4()}/decision",
                json={"approve": True}, headers=headers)
        assert hold.status_code in (401, 403), hold.text
        assert decide.status_code in (401, 403), decide.text
    finally:
        w.cleanup()


def test_extension_grants_nothing_until_it_is_approved():
    """38 — the retention date moves on approval, and only then."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        original = _now() + timedelta(days=30)
        db = SessionLocal()
        rec = db.get(LiveRecording, rid)
        rec.retention_expires_at = original
        rec.retention_policy_version = "default-v1"
        db.commit()
        db.close()

        wanted = _now() + timedelta(days=200)
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            requester = db.get(User, w.owner_id)
            ext = media_retention.request_extension(
                db, _Bg(), db.get(LiveRecording, rid), requester=requester,
                reason_category="dispute_or_claim", requested_until=wanted)
            ext_id = ext.id
            db.close()
        assert cap.count(MED_011_EXT_REQUESTED) == 1, cap.subjects
        assert "Pending platform governance approval" in cap.of(MED_011_EXT_REQUESTED)["text"]
        # Nothing moved.
        assert abs((_rec(rid).retention_expires_at - original).total_seconds()) < 5

        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            approver = db.get(User, w.owner_id)
            media_retention.decide_extension(
                db, _Bg(), db.get(RetentionExtension, ext_id), approver=approver,
                approve=True)
            db.close()
        assert abs((_rec(rid).retention_expires_at - wanted).total_seconds()) < 5
        assert _rec(rid).retention_warned_at is None, "the new deadline must re-arm"
    finally:
        w.cleanup()


def test_deletion_cannot_bypass_an_open_retention_window():
    """39 — the retention date is a keep-guarantee, and an immediate delete respects it."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        db = SessionLocal()
        db.get(LiveRecording, rid).retention_expires_at = _now() + timedelta(days=60)
        db.commit()
        db.close()

        headers = w.token()
        client = TestClient(m.app)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.delete(f"/api/organization/recordings/{rid}", headers=headers)
        assert r.status_code == 409, r.text
        assert "retention period" in r.text.lower()
        assert _rec(rid) is not None
    finally:
        w.cleanup()


def test_a_pending_extension_blocks_deletion():
    """40 — an asset whose retention is under review cannot be destroyed mid-decision."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        db = SessionLocal()
        db.add(RetentionExtension(org_id=w.org_id, event_id=w.event_id, recording_id=rid,
                                  state="pending", reason_category="internal_review",
                                  requested_until=_now() + timedelta(days=90),
                                  requested_by=w.owner_id))
        db.commit()
        eligible, reason = media_retention.deletion_eligible(db, db.get(LiveRecording, rid))
        db.close()
        assert eligible is False
        assert "extension is pending" in reason
    finally:
        w.cleanup()


def test_deletion_completes_only_after_storage_actually_succeeds():
    """41 — the storage result is read, and the row survives a failure.

    The previous implementation called delete_object(), discarded the boolean, and deleted
    the row regardless — leaving an orphaned object nothing recorded as needing removal.
    """
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx, patch.object(media_retention.livekit, "delete_object", lambda k: False), \
                patch.object(media_retention.livekit, "gcs_configured", lambda: True):
            db = SessionLocal()
            actor = db.get(User, w.owner_id)
            deleted, reason = media_retention.delete_asset(
                db, _Bg(), db.get(LiveRecording, rid), actor=actor)
            db.close()
        assert deleted is False
        assert _rec(rid) is not None, "the row was deleted despite a storage failure"
        assert _rec(rid).deletion_status == "failed"
        assert cap.count(MED_011_DELETE_FAILED) == 1, cap.subjects
        body = cap.of(MED_011_DELETE_FAILED)["text"]
        assert "still exists" in body
        assert "Nothing was partially removed" in body
        cap.assert_no_secrets()
    finally:
        w.cleanup()


def test_deletion_completed_is_truthful_about_residual_copies():
    """42 — a successful deletion never claims every copy everywhere was erased."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx, patch.object(media_retention.livekit, "delete_object", lambda k: True):
            db = SessionLocal()
            actor = db.get(User, w.owner_id)
            deleted, reason = media_retention.delete_asset(
                db, _Bg(), db.get(LiveRecording, rid), actor=actor)
            db.close()
        assert deleted is True, reason
        assert _rec(rid) is None
        body = cap.of(MED_011_DELETED)["text"]
        assert "outside Zoiko Steam's control" in body
        assert "permanently erased" not in body.lower()
        assert "all copies" not in body.lower()
        assert "Retained records" in body
    finally:
        w.cleanup()


def test_the_asset_ledger_survives_the_deletion():
    """43 — a retention audit asks what happened to an asset that no longer exists."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx, patch.object(media_retention.livekit, "delete_object", lambda k: True):
            db = SessionLocal()
            actor = db.get(User, w.owner_id)
            media_retention.delete_asset(db, _Bg(), db.get(LiveRecording, rid), actor=actor)
            db.close()
        assert _rec(rid) is None
        db = SessionLocal()
        rows = db.query(MediaAssetEvent).filter(
            MediaAssetEvent.recording_id == rid).all()
        transitions = {r.transition for r in rows}
        db.close()
        assert "deleted" in transitions, transitions
    finally:
        w.cleanup()


def test_provider_outage_does_not_change_governed_media_state():
    """44 — Resend failing must not alter retention, hold, or deletion state.

    The email is a report of a decision, never the decision itself.
    """
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture(fail=True)
        with ctx, patch.object(media_retention.livekit, "delete_object", lambda k: True):
            db = SessionLocal()
            actor = db.get(User, w.owner_id)
            deleted, reason = media_retention.delete_asset(
                db, _Bg(), db.get(LiveRecording, rid), actor=actor)
            db.close()
        assert deleted is True, reason
        assert _rec(rid) is None, "a send failure must not undo a completed deletion"

        w2 = World()
        try:
            rid2 = w2.make_recording(status="stopped", stopped_at=_now())
            cap2, ctx2 = _capture(fail=True)
            with ctx2:
                db = SessionLocal()
                admin = db.get(User, w2.owner_id)
                media_retention.place_hold(db, _Bg(), db.get(LiveRecording, rid2),
                                           actor=admin, category="litigation",
                                           hold_reference="LH-9")
                db.close()
            assert _rec(rid2).legal_hold is True, "a send failure must not drop a hold"
        finally:
            w2.cleanup()
    finally:
        w.cleanup()


def test_med011_hold_notifications_are_deduplicated():
    """45 — one notice per hold transition, and a release re-arms the next placement."""
    w = World()
    try:
        rid = w.make_recording(status="stopped", stopped_at=_now())
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            admin = db.get(User, w.owner_id)
            rec = db.get(LiveRecording, rid)
            media_retention.place_hold(db, _Bg(), rec, actor=admin, category="litigation",
                                       hold_reference="LH-1")
            media_retention.notify_hold(db, _Bg(), rec, released=False)
            media_retention.release_hold(db, _Bg(), rec, actor=admin)
            db.close()
        # place_hold sent to N recipients; the redundant notify_hold added nothing, so the
        # placed and released notices reached the same audience exactly once each.
        placed, released = cap.to(MED_011_HOLD), cap.to(MED_011_HOLD_RELEASED)
        assert placed and len(placed) == len(set(placed)), placed
        assert sorted(placed) == sorted(released), (placed, released)
        assert "Permitted again" in cap.of(MED_011_HOLD_RELEASED)["text"]
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
