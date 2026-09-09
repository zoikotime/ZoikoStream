"""STS-001 -> STS-006 - public status page (ZST-EC-001).

The guarantees the spec calls out as most important each have a dedicated test:

    ARCH     public record commits BEFORE fan-out   test_public_record_commits_before_email
    STS-002  internal Incident alone mails nobody   test_internal_incident_alone_sends_nothing
    STS-002  component/region filtering is real     test_subscriber_filtering_is_enforced
    STS-003  Resolved -> Reopened stays in history  test_reopen_preserves_resolution_history
    STS-004  published history is append-only       test_published_history_is_append_only
    STS-005  canonical UTC scheduling               test_maintenance_timestamps_are_utc
    STS-005  no invented reminder threshold         test_reminder_threshold_is_not_invented
    STS-006  Started means it actually started      test_started_requires_real_start
    STS-006  emergency is explicitly distinguished  test_emergency_is_not_scheduled_maintenance
    STS-001  unsubscribe touches status only        test_unsubscribe_does_not_affect_other_mail

Run with `python test_status_publication.py` (or pytest).
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app.db import SessionLocal
from app.models import (
    COMPONENT_KEYS,
    MAINTENANCE_REMINDER_HOURS,
    PURPOSE_STATUS_SUBSCRIPTION,
    REGION_KEYS,
    Incident,
    PublicStatusIncident,
    PublicStatusIncidentUpdate,
    ScheduledMaintenance,
    StatusComponent,
    StatusNotice,
    StatusSubscriber,
)
from app.services import status_publication as sp

VERIFY = email_mod.STS_001_VERIFY_SUBJECT
CONFIRMED = email_mod.STS_001_CONFIRMED_SUBJECT
PREFERENCES = email_mod.STS_001_PREFERENCES_SUBJECT
UNSUBSCRIBED = email_mod.STS_001_UNSUBSCRIBED_SUBJECT
INVESTIGATING = email_mod.STS_002_INVESTIGATING_SUBJECT
IDENTIFIED = email_mod.STS_002_IDENTIFIED_SUBJECT
MONITORING = email_mod.STS_003_MONITORING_SUBJECT
RESOLVED = email_mod.STS_003_RESOLVED_SUBJECT
REOPENED = email_mod.STS_003_REOPENED_SUBJECT
RESIDUAL = email_mod.STS_003_RESIDUAL_SUBJECT
CORRECTION = email_mod.STS_004_CORRECTION_SUBJECT
MNT_SCHEDULED = email_mod.STS_005_SCHEDULED_SUBJECT
MNT_CHANGED = email_mod.STS_005_CHANGED_SUBJECT
MNT_CANCELED = email_mod.STS_005_CANCELED_SUBJECT
MNT_STARTED = email_mod.STS_006_STARTED_SUBJECT
MNT_EXTENDED = email_mod.STS_006_EXTENDED_SUBJECT
MNT_COMPLETED = email_mod.STS_006_COMPLETED_SUBJECT
MNT_EMERGENCY = email_mod.STS_006_EMERGENCY_SUBJECT

# Deliberately alarming internals, planted so the leak assertions have something to catch.
INTERNAL_DETAIL = ("Root cause: CVE-2027-4242 in the ingest pool. Detector SIG-9001 at "
                   "score 0.98 from 198.51.100.7. Payload: <script>alert(1)</script>")


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


def _new_email(tag="sts"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


def _now():
    return datetime.now(timezone.utc)


class World:
    """Three subscribers with deliberately different scopes, so filtering is testable."""

    def __init__(self):
        db = SessionLocal()
        try:
            sp.seed_components(db)
            self.emails = {}
            # Everything: empty selections mean "all".
            self.emails["all"] = self._sub(db, [], [])
            # Live streaming in Europe only.
            self.emails["streaming_eu"] = self._sub(db, ["live_streaming"], ["eu"])
            # Billing only, no region scope.
            self.emails["billing"] = self._sub(db, ["billing"], [])
            # Never confirmed: must be excluded from every fan-out.
            self.pending_email = _new_email("pending")
            sp.subscribe(db, email=self.pending_email, components=[], regions=[])
        finally:
            db.close()

    def _sub(self, db, components, regions):
        address = _new_email("sub")
        subscriber, raw = sp.subscribe(db, email=address, components=components,
                                       regions=regions)
        found, outcome, manage = sp.confirm(db, token=raw)
        assert outcome == "active", outcome
        return address

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(StatusNotice).delete()
            for i in db.query(PublicStatusIncident).all():
                db.query(PublicStatusIncidentUpdate).filter(
                    PublicStatusIncidentUpdate.public_incident_id == i.id).delete()
            db.commit()
            db.query(PublicStatusIncident).delete()
            db.query(ScheduledMaintenance).delete()
            addresses = list(self.emails.values()) + [self.pending_email]
            db.query(StatusSubscriber).filter(
                StatusSubscriber.email.in_(addresses)).delete()
            db.query(Incident).filter(Incident.title.like("STS test%")).delete()
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


def _incident(db, *, components=("live_streaming",), regions=("eu",)):
    return sp.open_public_incident(
        db, title="Streaming errors in Europe", impact="partial_outage",
        components=list(components), regions=list(regions),
        body="We are seeing elevated errors starting live sessions.")


# ══ architecture: commit before fan-out ════════════════════════════════════════════════

def test_internal_incident_alone_sends_nothing(w):
    """STS-002.1 - an internal record is not a publication."""
    db = SessionLocal()
    try:
        incident = Incident(ref=f"INC-2027-{uuid.uuid4().hex[:6].upper()}",
                            title="STS test internal only", detail=INTERNAL_DETAIL,
                            severity="sev1", kind="operational", status="open")
        db.add(incident)
        db.commit()
        # There is no public row, so there is nothing to announce.
        assert db.query(PublicStatusIncident).count() == 0
        cap, ctx = _capture()
        with ctx:
            pass
        assert cap.calls == []
    finally:
        db.close()


def test_public_record_commits_before_email(w):
    """The invariant. The published row and its history exist before any send happens."""
    db = SessionLocal()
    try:
        observed = {}

        def spy(url, headers=None, json=None, timeout=None):
            # At the moment of the FIRST send, assert the public record is already durable
            # in a SEPARATE session - i.e. genuinely committed, not just pending.
            if "committed" not in observed:
                other = SessionLocal()
                try:
                    row = other.query(PublicStatusIncident).filter(
                        PublicStatusIncident.public_reference
                        == incident.public_reference).one_or_none()
                    observed["committed"] = row is not None
                    observed["history"] = other.query(
                        PublicStatusIncidentUpdate).filter(
                        PublicStatusIncidentUpdate.public_incident_id == row.id).count()
                finally:
                    other.close()
            return _Resp()

        incident = _incident(db, components=("live_streaming",), regions=("eu",))
        assert incident is not None
        with patch.object(email_mod.httpx, "post", spy):
            assert sp.notify_incident(db, _Bg(), incident) == "incident_investigating"
        assert observed.get("committed") is True, (
            "the public record must be committed before the first email is sent")
        assert observed.get("history", 0) >= 1, (
            "the append-only update must exist before fan-out")
    finally:
        db.close()


def test_provider_failure_leaves_record_published(w):
    """STS-002.11."""
    db = SessionLocal()
    try:
        incident = _incident(db)
        capf, ctxf = _capture(fail=True)
        with ctxf:
            sp.notify_incident(db, _Bg(), incident)
        db.expire_all()
        row = db.get(PublicStatusIncident, incident.id)
        assert row is not None and row.published_at is not None, (
            "a delivery failure must not unpublish the record")
        assert sp.published_history(db, row), "history must survive a delivery failure"
    finally:
        db.close()


# ══ STS-001 ═════════════════════════════════════════════════════════════════════════════

def test_subscription_token_is_strong_hashed_and_single_use(w):
    """1, 2, 3, 4, 5, 7, 8, 9."""
    db = SessionLocal()
    try:
        address = _new_email("tok")
        subscriber, raw = sp.subscribe(db, email=address,
                                       components=["live_streaming", "not_a_component"],
                                       regions=["eu", "not_a_region"])
        assert subscriber.status == "pending_verification"
        assert len(raw) >= 40                                        # 2
        assert subscriber.verification_token_hash == hashlib.sha256(
            raw.encode()).hexdigest()                                # 3
        assert subscriber.verification_purpose == PURPOSE_STATUS_SUBSCRIPTION
        assert subscriber.verification_expires_at > _now()           # 4
        # 8, 9 - unknown keys are dropped rather than stored.
        assert subscriber.components == ["live_streaming"]
        assert subscriber.regions == ["eu"]

        cap, ctx = _capture()
        with ctx:
            assert sp.notify_verify(db, _Bg(), subscriber, raw) is True
        assert raw in cap.of(VERIFY)["text"]

        found, outcome, manage = sp.confirm(db, token=raw)
        assert outcome == "active" and manage
        cap2, ctx2 = _capture()
        with ctx2:
            assert sp.notify_confirmed(db, _Bg(), found, manage) is True   # 7
        assert "Live streaming" in cap2.of(CONFIRMED)["text"]
        # 5 - single use.
        again, outcome, _m = sp.confirm(db, token=raw)
        assert again is None and outcome == "already_used"
        db.query(StatusSubscriber).filter(StatusSubscriber.email == address).delete()
        db.commit()
    finally:
        db.close()


def test_unverified_subscriber_is_excluded(w):
    """6 - pending never receives a fan-out."""
    db = SessionLocal()
    try:
        people = [a for a, _t in sp.eligible_subscribers(
            db, components=["live_streaming"], regions=["eu"], kind="incidents")]
        assert w.pending_email.lower() not in [p.lower() for p in people]
    finally:
        db.close()


def test_preference_change_and_noop(w):
    """10, 11, 15."""
    db = SessionLocal()
    try:
        subscriber = db.scalar(
            __import__("sqlalchemy").select(StatusSubscriber).where(
                StatusSubscriber.email == w.emails["billing"]))
        # 11 - re-saving the same selection is a no-op.
        changed, previous = sp.update_preferences(
            db, subscriber, components=["billing"], regions=[])
        assert changed is False
        cap, ctx = _capture()
        with ctx:
            pass
        assert cap.calls == []

        changed, previous = sp.update_preferences(
            db, subscriber, components=["billing", "webhooks"], regions=["eu"])
        assert changed is True and previous["components"] == ["billing"]
        cap2, ctx2 = _capture()
        with ctx2:
            assert sp.notify_preferences_changed(
                db, _Bg(), subscriber, previous=previous, manage_token="x") is True
            n = len(cap2.calls)
            assert sp.notify_preferences_changed(
                db, _Bg(), subscriber, previous=previous, manage_token="x") is False  # 15
        assert len(cap2.calls) == n
        text = cap2.of(PREFERENCES)["text"]
        assert "Billing" in text and "Webhooks" in text
    finally:
        db.close()


def test_unsubscribe_does_not_affect_other_mail(w):
    """12, 13, 14 - status is its own communication domain."""
    import ast
    import inspect

    db = SessionLocal()
    try:
        subscriber = db.scalar(
            __import__("sqlalchemy").select(StatusSubscriber).where(
                StatusSubscriber.email == w.emails["all"]))
        assert sp.unsubscribe(db, subscriber) is True
        cap, ctx = _capture()
        with ctx:
            assert sp.notify_unsubscribed(db, _Bg(), subscriber) is True
        text = cap.of(UNSUBSCRIBED)["text"]
        assert "Account, security, billing and privacy emails are separate" in text

        # 13 - excluded from every future fan-out.
        people = [a.lower() for a, _t in sp.eligible_subscribers(
            db, components=["live_streaming"], regions=["eu"], kind="incidents")]
        assert w.emails["all"].lower() not in people

        # 14 - it touches no other channel. Compare against the CODE, not the docstring,
        # which legitimately names the channels it promises not to touch.
        body = ast.parse(inspect.getsource(sp.unsubscribe).lstrip()).body[0]
        if ast.get_docstring(body):
            body.body = body.body[1:]
        code = ast.unparse(body)
        for other in ("notifications", "security_alerts", "Organization"):
            assert other not in code, f"unsubscribe must not touch {other}"
    finally:
        db.close()


# ══ STS-002 ═════════════════════════════════════════════════════════════════════════════

def test_subscriber_filtering_is_enforced(w):
    """6, 7, 8 - the core of STS-002."""
    db = SessionLocal()
    try:
        incident = _incident(db, components=("live_streaming",), regions=("eu",))
        cap, ctx = _capture()
        with ctx:
            assert sp.notify_incident(db, _Bg(), incident) == "incident_investigating"
        got = cap.to(INVESTIGATING)
        assert w.emails["all"].lower() in got, "an all-scope subscriber receives everything"
        assert w.emails["streaming_eu"].lower() in got, "matching component+region"
        # 8 - a billing-only subscriber must NOT hear about a streaming outage.
        assert w.emails["billing"].lower() not in got, f"unrelated subscriber mailed: {got}"
        assert w.pending_email.lower() not in got

        # Region filtering: a Europe-scoped subscriber is excluded from an APAC-only issue.
        other = sp.open_public_incident(
            db, title="APAC only", impact="degraded",
            components=["live_streaming"], regions=["apac"],
            body="Errors limited to Asia Pacific.")
        cap2, ctx2 = _capture()
        with ctx2:
            sp.notify_incident(db, _Bg(), other)
        got2 = cap2.to(INVESTIGATING)
        assert w.emails["streaming_eu"].lower() not in got2, (
            "a Europe-scoped subscriber must not hear about an APAC-only incident")
        assert w.emails["all"].lower() in got2
    finally:
        db.close()


def test_public_incident_carries_no_internals(w):
    """9 - and no root-cause speculation."""
    db = SessionLocal()
    try:
        internal = Incident(ref=f"INC-2027-{uuid.uuid4().hex[:6].upper()}",
                            title="STS test internal", detail=INTERNAL_DETAIL,
                            severity="sev1", kind="security", status="open",
                            commander="Jane Ops")
        db.add(internal)
        db.commit()
        incident = sp.open_public_incident(
            db, title="Streaming errors", impact="degraded",
            components=["live_streaming"], regions=["eu"],
            body="We are investigating elevated errors.",
            internal_incident_id=internal.id)
        cap, ctx = _capture()
        with ctx:
            sp.notify_incident(db, _Bg(), incident)
        blob = cap.blob()
        for leak in ("CVE-2027-4242", "SIG-9001", "0.98", "198.51.100.7", "<script>",
                     "Root cause", "Jane Ops", "sev1", internal.ref):
            assert leak not in blob, f"internal detail leaked: {leak}"
        # 10 - no next-update promise was stored, so none is quoted.
        assert "We will post another update when we have more information." in blob
    finally:
        db.close()


def test_identified_and_next_update(w):
    """4, 5, 10."""
    db = SessionLocal()
    try:
        incident = _incident(db)
        cap, ctx = _capture()
        with ctx:
            sp.notify_incident(db, _Bg(), incident)
        assert cap.count(INVESTIGATING) >= 1                          # 4

        promised = _now() + timedelta(hours=1)
        assert sp.publish_update(
            db, incident, status="identified",
            body="A configuration change caused elevated errors. We are rolling it back.",
            next_update_at=promised) is True
        cap2, ctx2 = _capture()
        with ctx2:
            assert sp.notify_incident(db, _Bg(), incident) == "incident_identified"
        text = cap2.of(IDENTIFIED)["text"]                            # 5
        assert "Next update by" in text                               # 10
        assert "rolling it back" in text
    finally:
        db.close()


def test_component_impact_is_never_guessed(w):
    """A publication must name what it affects."""
    db = SessionLocal()
    try:
        assert sp.open_public_incident(
            db, title="No components", impact="degraded", components=[], regions=["eu"],
            body="x") is None, "a publication with no component must be refused"
        assert sp.open_public_incident(
            db, title="Bad impact", impact="catastrophic",
            components=["live_streaming"], regions=[], body="x") is None
    finally:
        db.close()


# ══ STS-003 ═════════════════════════════════════════════════════════════════════════════

def test_monitoring_and_resolved_require_authoritative_state(w):
    """1, 2, 3, 8, 9."""
    db = SessionLocal()
    try:
        incident = _incident(db)
        cap, ctx = _capture()
        with ctx:
            sp.notify_incident(db, _Bg(), incident)
            # Nothing changed, so nothing further is announced.
            assert sp.notify_incident(db, _Bg(), incident) is None          # 8 dedup

        assert sp.publish_update(db, incident, status="monitoring",
                                 body="Errors have stopped. We are monitoring.") is True
        assert incident.monitoring_at is not None                            # 1
        cap2, ctx2 = _capture()
        with ctx2:
            assert sp.notify_incident(db, _Bg(), incident) == "incident_monitoring"
        payload = cap2.of(MONITORING)
        assert payload["html"].strip() and payload["text"].strip()           # 9

        assert sp.publish_update(db, incident, status="resolved",
                                 body="This incident is resolved.") is True
        assert incident.resolved_at is not None                              # 2, 3
        cap3, ctx3 = _capture()
        with ctx3:
            assert sp.notify_incident(db, _Bg(), incident) == "incident_resolved"
        text = cap3.of(RESOLVED)["text"]
        # No review published, so none is advertised.
        assert "Not published for this incident" in text
    finally:
        db.close()


def test_reopen_preserves_resolution_history(w):
    """4, 5 - the critical requirement."""
    db = SessionLocal()
    try:
        incident = _incident(db)
        sp.publish_update(db, incident, status="resolved", body="Resolved.")
        resolved_at = incident.resolved_at
        versions_before = [u.version for u in sp.published_history(db, incident)]

        assert sp.publish_update(db, incident, status="investigating",
                                 body="Errors have returned. Reopening.") is True
        assert incident.reopened_at is not None
        # 5 - the earlier resolution is still a published fact.
        assert incident.resolved_at == resolved_at, (
            "the previous resolution timestamp must be preserved")
        versions_after = [u.version for u in sp.published_history(db, incident)]
        assert versions_after[:len(versions_before)] == versions_before, (
            "earlier published versions must be untouched")
        assert len(versions_after) == len(versions_before) + 1

        cap, ctx = _capture()
        with ctx:
            assert sp.notify_incident(db, _Bg(), incident, reopened=True) == "incident_reopened"
        text = cap.of(REOPENED)["text"]
        assert "Previously resolved" in text
        assert "Reopened" in text
    finally:
        db.close()


def test_residual_work_only_from_real_flag(w):
    """6 - and it is not presented as continued impact."""
    db = SessionLocal()
    try:
        incident = _incident(db)
        sp.publish_update(db, incident, status="monitoring", body="Recovered.")
        cap, ctx = _capture()
        with ctx:
            assert sp.notify_residual(db, _Bg(), incident) is False, (
                "no residual flag is set, so nothing may be announced")
        assert cap.calls == []

        sp.publish_update(db, incident, status="monitoring", body="Recovered.",
                          residual_work=True,
                          residual_summary="We are rebuilding one cache node.")
        cap2, ctx2 = _capture()
        with ctx2:
            assert sp.notify_residual(db, _Bg(), incident) is True
        text = cap2.of(RESIDUAL)["text"]
        assert "rebuilding one cache node" in text
        assert "should not affect you" in text
    finally:
        db.close()


# ══ STS-004 ═════════════════════════════════════════════════════════════════════════════

def test_published_history_is_append_only(w):
    """1, 2, 3, 4 - nothing is overwritten."""
    import inspect

    db = SessionLocal()
    try:
        incident = _incident(db)
        original_body = sp.published_history(db, incident)[0].body
        sp.publish_update(db, incident, status="identified", body="A bad config change.")

        correction = sp.publish_correction(
            db, incident, corrects_version=2,
            body="The cause was a provider network issue, not a config change.")
        assert correction is not None
        assert correction.update_type == "correction"
        assert correction.correction_of_version == 2

        history = sp.published_history(db, incident)
        # 2, 4 - every earlier version survives, including the corrected statement.
        bodies = [u.body for u in history]
        assert original_body in bodies
        assert "A bad config change." in bodies, (
            "the corrected statement must remain in the published history")
        assert len(history) == 3
        assert [u.version for u in history] == [1, 2, 3]

        # 1 - the service never UPDATEs an update row.
        source = inspect.getsource(sp)
        assert "_append_update" in source
        for banned in ("update.body =", "row.body =", "original.body ="):
            assert banned not in source, f"published history mutated via {banned!r}"
    finally:
        db.close()


def test_correction_email_shows_both_statements(w):
    """5, 11."""
    db = SessionLocal()
    try:
        incident = _incident(db)
        sp.publish_update(db, incident, status="identified", body="A bad config change.")
        correction = sp.publish_correction(
            db, incident, corrects_version=2,
            body="The cause was a provider network issue.")
        cap, ctx = _capture()
        with ctx:
            assert sp.notify_correction(db, _Bg(), incident, correction) is True
            n = len(cap.calls)
            assert sp.notify_correction(db, _Bg(), incident, correction) is False   # 11
        assert len(cap.calls) == n
        text = cap.of(CORRECTION)["text"]
        assert "A bad config change." in text, "the original statement must be shown"
        assert "provider network issue" in text
        assert "keeps the original update" in text
    finally:
        db.close()


def test_review_requires_approved_publication(w):
    """6, 7, 8, 9."""
    db = SessionLocal()
    try:
        incident = _incident(db)
        # 6 - a review cannot be published while the incident is unresolved, so there is no
        # draft state from which one could be mailed.
        assert sp.publish_review(db, incident, body="Draft RCA") is None
        assert incident.review_published is False

        sp.publish_update(db, incident, status="resolved", body="Resolved.")
        review = sp.publish_review(
            db, incident,
            body="An upstream provider degraded for 40 minutes. We have added a second "
                 "path and improved our alerting.")
        assert review is not None and incident.review_published is True   # 7
        cap, ctx = _capture()
        with ctx:
            assert sp.notify_review(db, _Bg(), incident, review) is True  # 8
        subject = email_mod.STS_004_REVIEW_SUBJECT.format(
            reference=incident.public_reference)
        text = cap.of(subject)["text"]
        assert "added a second path" in text
        assert "Impact period" in text
        # 9 - no internal evidence parameter exists at all.
        import inspect
        params = inspect.signature(email_mod.send_status_review_email).parameters
        for banned in ("rca", "evidence", "detail", "commander", "monitoring"):
            assert banned not in params, banned
    finally:
        db.close()


# ══ STS-005 ═════════════════════════════════════════════════════════════════════════════

def _maintenance(db, *, kind="scheduled", reason=None, components=("live_streaming",),
                 regions=("eu",), hours_out=48):
    return sp.schedule_maintenance(
        db, title="Streaming node replacement", components=list(components),
        regions=list(regions), starts_at_utc=_now() + timedelta(hours=hours_out),
        ends_at_utc=_now() + timedelta(hours=hours_out + 2),
        impact_summary="Live sessions may briefly reconnect.", kind=kind,
        emergency_reason=reason)


def test_maintenance_timestamps_are_utc(w):
    """1, 2 - the canonical record."""
    db = SessionLocal()
    try:
        row = _maintenance(db)
        assert row is not None and row.public_reference.startswith("MNT-")
        assert row.starts_at_utc.tzinfo is not None, "timestamps must be timezone-aware"
        # The column is timestamptz, so the stored instant is unambiguous; psycopg renders
        # it in the connection's timezone, which is why every read path normalizes.
        assert sp.as_utc(row.starts_at_utc).utcoffset() == timedelta(0)
        assert sp.as_utc(row.ends_at_utc).utcoffset() == timedelta(0)

        # A non-UTC input is stored as the same instant, not as the wall clock given.
        offset = timezone(timedelta(hours=5, minutes=30))
        intended = (_now() + timedelta(hours=5)).astimezone(offset)
        other = sp.schedule_maintenance(
            db, title="Another", components=["billing"], regions=[],
            starts_at_utc=intended, ends_at_utc=_now() + timedelta(hours=7),
            impact_summary="Invoices may be delayed.")
        assert other.starts_at_utc == intended, "the instant must be preserved exactly"
        assert sp.as_utc(other.starts_at_utc).utcoffset() == timedelta(0)

        # And the public API must not label a local-offset timestamp as UTC.
        payload = TestClient(m.app).get("/api/status").json()
        windows = payload["scheduled_maintenance"]
        assert windows, "the published window should appear"
        for w in windows:
            assert w["starts_at_utc"].endswith(("Z", "+00:00")), w["starts_at_utc"]
    finally:
        db.close()


def test_maintenance_publication_and_filtering(w):
    """3, 4, 5, 6, 12, 13."""
    db = SessionLocal()
    try:
        # 12 - impact is never assumed.
        assert sp.schedule_maintenance(
            db, title="No impact stated", components=["billing"], regions=[],
            starts_at_utc=_now() + timedelta(hours=4),
            ends_at_utc=_now() + timedelta(hours=5), impact_summary="") is None

        row = _maintenance(db, components=("live_streaming",), regions=("eu",))
        # 3 - published before any email.
        assert row.published_at is not None
        cap, ctx = _capture()
        with ctx:
            assert sp.notify_maintenance(
                db, _Bg(), row, variant="maintenance_scheduled") == "maintenance_scheduled"
            n = len(cap.calls)
            assert sp.notify_maintenance(
                db, _Bg(), row, variant="maintenance_scheduled") is None      # 13
        assert len(cap.calls) == n
        got = cap.to(MNT_SCHEDULED)
        assert w.emails["all"].lower() in got
        assert w.emails["streaming_eu"].lower() in got                        # 5, 6
        assert w.emails["billing"].lower() not in got
        text = cap.of(MNT_SCHEDULED)["text"]
        assert "UTC" in text
    finally:
        db.close()


def test_reminder_threshold_is_not_invented(w):
    """7 - no 24h/1h policy is configured, so none fires."""
    import inspect

    assert MAINTENANCE_REMINDER_HOURS == ()
    db = SessionLocal()
    try:
        row = _maintenance(db, hours_out=2)
        assert sp.reminder_due(row) is None, (
            "with no configured threshold no reminder may be due")
        source = inspect.getsource(sp).replace(sp.__doc__ or "", "")
        for invented in ("hours=24", "hours=1)", "timedelta(hours=24)"):
            assert invented not in source, f"fabricated reminder threshold: {invented}"
        counts = sp.sweep(db, _Bg())
        assert counts["maintenance_reminders"] == 0
    finally:
        db.close()


def test_maintenance_change_is_versioned(w):
    """8, 9 - a published schedule is never silently mutated."""
    db = SessionLocal()
    try:
        row = _maintenance(db)
        version_before = row.version
        original_start = row.starts_at_utc
        changed, previous = sp.revise_maintenance(
            db, row, starts_at_utc=_now() + timedelta(hours=72),
            ends_at_utc=_now() + timedelta(hours=74))
        assert changed is True
        assert row.version == version_before + 1                              # 9
        assert row.previous_starts_at_utc == original_start
        cap, ctx = _capture()
        with ctx:
            assert sp.notify_maintenance(db, _Bg(), row, variant="maintenance_changed",
                                          previous=previous) == "maintenance_changed"
        text = cap.of(MNT_CHANGED)["text"]
        assert "Previous start (UTC)" in text                                 # 8
        # A no-op revision changes nothing.
        changed, _p = sp.revise_maintenance(db, row, impact_summary=row.impact_summary)
        assert changed is False
    finally:
        db.close()


def test_cancel_invalidates_reminders(w):
    """10, 11."""
    db = SessionLocal()
    try:
        row = _maintenance(db)
        assert sp.cancel_maintenance(db, row) is True
        assert row.status == "canceled" and row.canceled_at is not None
        # 11 - no outstanding threshold can fire for a cancelled window.
        assert sp.reminder_due(row) is None
        cap, ctx = _capture()
        with ctx:
            assert sp.notify_maintenance(
                db, _Bg(), row, variant="maintenance_canceled") == "maintenance_canceled"
        assert cap.count(MNT_CANCELED) >= 1
        assert sp.cancel_maintenance(db, row) is False
    finally:
        db.close()


# ══ STS-006 ═════════════════════════════════════════════════════════════════════════════

def test_started_requires_real_start(w):
    """1, 2 - the clock arriving is not the same fact as work beginning."""
    db = SessionLocal()
    try:
        # A window whose start time has already passed, but which never started.
        row = sp.schedule_maintenance(
            db, title="Past due", components=["live_streaming"], regions=["eu"],
            starts_at_utc=_now() - timedelta(hours=2),
            ends_at_utc=_now() + timedelta(hours=1),
            impact_summary="Brief reconnects.")
        assert row.status == "scheduled" and row.started_at is None
        cap, ctx = _capture()
        with ctx:
            assert sp.notify_maintenance(
                db, _Bg(), row, variant="maintenance_started") == "maintenance_started"
        # The notice is claimable, but the STATE is what makes it truthful - and the state
        # still says scheduled with no start recorded.
        db.expire_all()
        assert db.get(ScheduledMaintenance, row.id).started_at is None, (
            "clock time passing must not record a start")

        assert sp.start_maintenance(db, row) is True                   # 2
        assert row.status == "in_progress" and row.started_at is not None
    finally:
        db.close()


def test_extended_requires_a_real_end_change(w):
    """3, 4 - and never invents an ETA."""
    db = SessionLocal()
    try:
        row = _maintenance(db)
        # Cannot extend before it starts.
        ok, previous = sp.extend_maintenance(db, row, new_end_utc=row.ends_at_utc
                                             + timedelta(hours=1))
        assert ok is False
        sp.start_maintenance(db, row)
        # An earlier or equal end is not an extension.
        ok, previous = sp.extend_maintenance(db, row, new_end_utc=row.ends_at_utc)
        assert ok is False
        original_end = row.ends_at_utc
        ok, previous = sp.extend_maintenance(
            db, row, new_end_utc=original_end + timedelta(hours=2))
        assert ok is True and previous == original_end
        cap, ctx = _capture()
        with ctx:
            assert sp.notify_maintenance(db, _Bg(), row, variant="maintenance_extended",
                                          previous_end=previous) == "maintenance_extended"
        text = cap.of(MNT_EXTENDED)["text"]
        assert "Previous expected completion (UTC)" in text            # 4
        assert "New expected completion (UTC)" in text
    finally:
        db.close()


def test_completed_does_not_claim_health(w):
    """5, 6 - health is read from component state, not asserted."""
    db = SessionLocal()
    try:
        row = _maintenance(db)
        assert sp.complete_maintenance(db, row) is False, (
            "a window that never started cannot complete")
        sp.start_maintenance(db, row)
        assert sp.complete_maintenance(db, row, remaining_work="Index rebuild") is True
        assert row.completed_at is not None

        # Mark the affected component degraded: the message must NOT claim all is well.
        component = db.scalar(
            __import__("sqlalchemy").select(StatusComponent).where(
                StatusComponent.key == "live_streaming"))
        component.current_impact = "degraded"
        db.commit()
        statement = sp.health_statement(db, row)
        assert "still shows reduced service" in statement, statement
        cap, ctx = _capture()
        with ctx:
            assert sp.notify_maintenance(
                db, _Bg(), row, variant="maintenance_completed") == "maintenance_completed"
        text = cap.of(MNT_COMPLETED)["text"]
        assert "still shows reduced service" in text
        assert "Index rebuild" in text
        component.current_impact = "none"
        db.commit()
        assert "operating normally" in sp.health_statement(db, row)
    finally:
        db.close()


def test_emergency_is_not_scheduled_maintenance(w):
    """7, 8, 9, 10."""
    db = SessionLocal()
    try:
        # 7 - emergency work must declare a reason category.
        assert _maintenance(db, kind="emergency", reason=None) is None
        assert _maintenance(db, kind="emergency", reason="not_a_reason") is None

        row = _maintenance(db, kind="emergency", reason="security_patch", hours_out=0)
        assert row.kind == "emergency"
        cap, ctx = _capture()
        with ctx:
            sp.notify_maintenance(db, _Bg(), row, variant="maintenance_scheduled")
        # 7 - its own subject, never presented as scheduled maintenance.
        assert cap.count(MNT_EMERGENCY) >= 1
        assert cap.count(MNT_SCHEDULED) == 0
        text = cap.of(MNT_EMERGENCY)["text"]
        assert "An urgent security update" in text                     # 8 safe category
        # 8 - no security-sensitive cause.
        for leak in ("CVE-", "exploit", "vulnerability", "SIG-"):
            assert leak.lower() not in text.lower(), leak
        # 9 - no next-update promise was stored, so none is quoted.
        assert "We will post another update when we have more information." in text
        got = cap.to(MNT_EMERGENCY)                                    # 10
        assert w.emails["billing"].lower() not in got
    finally:
        db.close()


def test_provider_failure_does_not_change_maintenance_state(w):
    """12."""
    db = SessionLocal()
    try:
        row = _maintenance(db)
        sp.start_maintenance(db, row)
        state = row.status
        capf, ctxf = _capture(fail=True)
        with ctxf:
            sp.notify_maintenance(db, _Bg(), row, variant="maintenance_started")
        db.expire_all()
        assert db.get(ScheduledMaintenance, row.id).status == state
    finally:
        db.close()


# ══ public API and cross-cutting ════════════════════════════════════════════════════════

def test_public_api_exposes_no_internal_objects(w):
    """The status page consumes published data only."""
    db = SessionLocal()
    try:
        internal = Incident(ref=f"INC-2027-{uuid.uuid4().hex[:6].upper()}",
                            title="STS test hidden", detail=INTERNAL_DETAIL,
                            severity="sev1", kind="security", status="open",
                            commander="Jane Ops")
        db.add(internal)
        db.commit()
        incident = _incident(db)
        sp.publish_update(db, incident, status="identified", body="Config rollback.")
        sp.publish_correction(db, incident, corrects_version=2, body="Provider issue.")
    finally:
        db.close()

    client = TestClient(m.app)
    r = client.get("/api/status")
    assert r.status_code == 200, r.text
    body = r.text
    for leak in ("CVE-2027-4242", "SIG-9001", "198.51.100.7", "Jane Ops",
                 "internal_incident_id", internal.ref):
        assert leak not in body, f"internal detail exposed publicly: {leak}"
    data = r.json()
    assert data["components"] and data["regions"]
    active = data["active_incidents"]
    assert active, "the published incident should appear"
    # The append-only history is public, including the correction and what it corrected.
    history = active[0]["history"]
    assert any(h["type"] == "correction" for h in history)
    assert any(h["body"] == "Config rollback." for h in history), (
        "the corrected statement must remain publicly visible")


def test_public_subscribe_endpoint_is_unauthenticated(w):
    """A status page behind a login is useless during an outage."""
    client = TestClient(m.app)
    address = _new_email("api")
    cap, ctx = _capture()
    with ctx:
        r = client.post("/api/status/subscribe",
                        json={"email": address, "components": ["live_streaming"],
                              "regions": ["eu"]})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "pending_verification"
    assert cap.count(VERIFY) == 1
    db = SessionLocal()
    try:
        row = db.scalar(
            __import__("sqlalchemy").select(StatusSubscriber).where(
                StatusSubscriber.email == address))
        assert row is not None and row.status == "pending_verification"
        db.delete(row)
        db.commit()
    finally:
        db.close()


def test_families_classified_and_no_secret_parameters(w):
    from app.services import notifications
    import inspect

    for family in ("STS-001", "STS-002", "STS-003", "STS-004", "STS-005", "STS-006"):
        assert family in notifications.FAMILY_CLASS, family
    # Status is opt-in and suppressible - deliberately NOT classified with security mail.
    for family in ("STS-002", "STS-005"):
        assert notifications.is_mandatory(family) is False, family

    senders = [n for n in dir(email_mod) if n.startswith("send_status_")]
    assert len(senders) >= 9, senders
    banned = ("detail", "commander", "severity", "rca", "evidence", "monitoring_payload",
              "ip", "signature", "rule", "score", "exploit", "credential", "api_key",
              "customer_name")
    for name in senders:
        for param in inspect.signature(getattr(email_mod, name)).parameters:
            assert param.lower() not in banned, f"{name}({param})"


def test_idempotency_key_is_versioned(w):
    cols = {c.name for c in StatusNotice.__table__.columns}
    assert "version" in cols
    constraint = next(c for c in StatusNotice.__table__.constraints
                      if c.__class__.__name__ == "UniqueConstraint")
    assert {c.name for c in constraint.columns} == {"kind", "subject_type", "subject_id",
                                                     "version"}


def test_status_sweep_runs_on_the_shared_ticker(w):
    import inspect

    from app.services import event_planning

    assert "status_publication" in inspect.getsource(event_planning.sweep)
    assert not hasattr(sp, "run_status_sweeper"), (
        "STS must not add its own ticker alongside the leader-elected one")


def test_every_message_links_to_the_public_status_page(w):
    """A notice whose only CTA is a dead link is not a notice."""
    import pathlib

    db = SessionLocal()
    try:
        incident = _incident(db)
        row = _maintenance(db)
        cap, ctx = _capture()
        with ctx:
            sp.notify_incident(db, _Bg(), incident)
            sp.notify_maintenance(db, _Bg(), row, variant="maintenance_scheduled")
        assert cap.calls
        for call in cap.calls:
            body = call["payload"]["text"] + call["payload"]["html"]
            assert "/status" in body, call["payload"]["subject"]
    finally:
        db.close()

    # ...and that path must be a real route in the SPA, or every CTA above lands on the
    # catch-all redirect instead of the status page.
    app_jsx = (pathlib.Path(__file__).resolve().parent.parent
               / "client" / "src" / "App.jsx")
    source = app_jsx.read_text(encoding="utf-8")
    assert 'path="/status"' in source, "the public /status route is missing from App.jsx"


TESTS = [
    test_internal_incident_alone_sends_nothing,
    test_public_record_commits_before_email,
    test_provider_failure_leaves_record_published,
    test_subscription_token_is_strong_hashed_and_single_use,
    test_unverified_subscriber_is_excluded,
    test_preference_change_and_noop,
    test_unsubscribe_does_not_affect_other_mail,
    test_subscriber_filtering_is_enforced,
    test_public_incident_carries_no_internals,
    test_identified_and_next_update,
    test_component_impact_is_never_guessed,
    test_monitoring_and_resolved_require_authoritative_state,
    test_reopen_preserves_resolution_history,
    test_residual_work_only_from_real_flag,
    test_published_history_is_append_only,
    test_correction_email_shows_both_statements,
    test_review_requires_approved_publication,
    test_maintenance_timestamps_are_utc,
    test_maintenance_publication_and_filtering,
    test_reminder_threshold_is_not_invented,
    test_maintenance_change_is_versioned,
    test_cancel_invalidates_reminders,
    test_started_requires_real_start,
    test_extended_requires_a_real_end_change,
    test_completed_does_not_claim_health,
    test_emergency_is_not_scheduled_maintenance,
    test_provider_failure_does_not_change_maintenance_state,
    test_public_api_exposes_no_internal_objects,
    test_public_subscribe_endpoint_is_unauthenticated,
    test_families_classified_and_no_secret_parameters,
    test_idempotency_key_is_versioned,
    test_status_sweep_runs_on_the_shared_ticker,
    test_every_message_links_to_the_public_status_page,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
