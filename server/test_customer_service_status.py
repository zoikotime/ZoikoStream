"""/organization/support's Service health: only what the platform can actually check.

The customer page used to list all eight internal lifecycle stages, so it read "Transcoding &
packaging — Not configured — Not measured" (a stage whose only member, "workers", is hard-coded
"Not yet integrated"), and an "Incident-free (24 h)" column of 100.00% that was arithmetic over an
empty incident log. services/org.customer_status now returns three customer services — Live
streaming, Recording & replay, Management API — each with a status from real probes, recorded
incidents and (recordings) the organization's own recent recording starts; a service with no
current verdict is left out, never shown as "not configured".

Also pinned: another organization's incident no longer moves this one's status (it used to,
through ops.stage_health and ops.availability); Recording & replay reads Operational only on
positive evidence ("idle" otherwise); an operational incident with no stage, and a published
public-status incident, reach the page without anything being guessed; internal incident titles
never do; and a failure in this subsystem never takes /organization/overview down.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import AuditLog, Event, Incident, LiveRecording, Organization, PublicStatusIncident, User
from app.security import create_access_token, hash_password
from app.services import admin as admin_svc
from app.services import bus
from app.services import org as org_svc

UTC = timezone.utc
H = timedelta(hours=1)
INTERNAL_TERMS = ("not_configured", "unmonitored", "Not configured", "Not measured", "Transcoding",
                  "packaging", "produce", "workers", "CDN")


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")


def health(streaming="ok", storage="ok", database="ok"):
    """A platform_health snapshot: the probes customer_status reads, plus the two that are
    hard-coded "not_configured" by design."""
    probes = {"database": database, "api": "ok", "auth": "ok", "streaming": streaming, "storage": storage,
              "email": "ok", "cdn": "not_configured", "workers": "not_configured"}
    return {"services": [{"id": sid, "name": sid.title(), "status": st} for sid, st in probes.items()]}


class World:
    def __init__(self, label="A"):
        db = SessionLocal()
        try:
            org = Organization(name=f"Status {label} {uuid.uuid4().hex[:6]}", status="active")
            db.add(org)
            db.flush()
            admin = User(org_id=org.id, full_name=f"{label} Admin", role="org_admin", is_active=True,
                         email=f"st-{uuid.uuid4().hex[:10]}@example.com", username=f"st{uuid.uuid4().hex[:10]}",
                         password_hash=hash_password("x"), email_verified=True)
            db.add(admin)
            db.flush()
            ev = Event(org_id=org.id, created_by=admin.id, title="Status event", status="ended",
                       visibility="public", start_time=datetime.now(UTC) - 5 * H)
            db.add(ev)
            db.commit()
            self.org, self.admin, self.event = org.id, admin.id, ev.id
            self.incidents = []
            self.published = []
        finally:
            db.close()

    def incident(self, stage, severity="sev3", *, scope="org", started=None, resolved=None, title=None,
                 kind="operational"):
        db = SessionLocal()
        try:
            inc = Incident(ref=f"INC-T-{uuid.uuid4().hex[:10]}", title=title or f"{stage} trouble",
                           severity=severity, stage=stage, kind=kind,
                           org_id=self.org if scope == "org" else None,
                           status="resolved" if resolved else "open",
                           started_at=started or datetime.now(UTC) - H, resolved_at=resolved)
            db.add(inc)
            db.commit()
            self.incidents.append(inc.id)
            return inc.id
        finally:
            db.close()

    def recording_start(self, *, ok, ago=timedelta(minutes=10), at=None):
        db = SessionLocal()
        try:
            db.add(LiveRecording(event_id=self.event, org_id=self.org,
                                 status="recording" if ok else "failed",
                                 egress_id="EG_ok" if ok else None,
                                 error=None if ok else "ServerError(code=unavailable, status=503)",
                                 created_at=at or datetime.now(UTC) - ago))
            db.commit()
        finally:
            db.close()

    def publish(self, title, impact, components, *, published=True, resolved=False):
        """A public status-page incident (platform-wide by nature)."""
        db = SessionLocal()
        try:
            now = datetime.now(UTC)
            row = PublicStatusIncident(title=title, impact=impact, affected_components=components,
                                       affected_regions=["global"], started_at=now - H,
                                       status="resolved" if resolved else "investigating",
                                       resolved_at=now - timedelta(minutes=5) if resolved else None,
                                       published_at=now - H if published else None)
            db.add(row)
            db.commit()
            self.published.append(row.id)
        finally:
            db.close()

    def status(self, snapshot=None):
        db = SessionLocal()
        try:
            now = datetime.now(UTC)
            return org_svc.customer_status(db, self.org, snapshot or health(), now - 24 * H, now)
        finally:
            db.close()

    def lifecycle(self, snapshot=None):
        db = SessionLocal()
        try:
            return {s["stage"]: s for s in org_svc.stage_health(db, self.org, datetime.now(UTC) - 24 * H,
                                                                 health=snapshot or health())}
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(LiveRecording).filter(LiveRecording.event_id == self.event).delete(synchronize_session=False)
            if self.incidents:
                db.query(Incident).filter(Incident.id.in_(self.incidents)).delete(synchronize_session=False)
            if self.published:
                db.query(PublicStatusIncident).filter(
                    PublicStatusIncident.id.in_(self.published)).delete(synchronize_session=False)
            db.query(Event).filter(Event.id == self.event).delete(synchronize_session=False)
            db.query(AuditLog).filter(AuditLog.org_id == self.org).delete(synchronize_session=False)
            db.query(User).filter(User.id == self.admin).delete(synchronize_session=False)
            db.query(Organization).filter(Organization.id == self.org).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


@pytest.fixture
def w():
    world = World("A")
    try:
        yield world
    finally:
        world.cleanup()


@pytest.fixture
def other():
    world = World("B")
    try:
        yield world
    finally:
        world.cleanup()


def by_id(status):
    return {s["id"]: s for s in status["services"]}


# ── what is shown ───────────────────────────────────────────────────────────────────────

def test_three_customer_services_from_real_checks(w):
    w.recording_start(ok=True)
    out = w.status()
    assert [s["id"] for s in out["services"]] == ["live_streaming", "recording", "platform_api"]
    assert [s["name"] for s in out["services"]] == ["Live streaming", "Recording & replay", "Management API"]
    assert all(s["status"] == "ok" and s["incidents_24h"] == 0 for s in out["services"])
    assert out["verdict"] == "ok" and out["platform_notice"] is None


def test_no_internal_terms_or_transcoding_reach_the_customer(w):
    dumped = json.dumps(w.status())
    for term in INTERNAL_TERMS:
        assert term not in dumped


def test_an_unconfigured_service_is_left_out_and_the_verdict_is_not_green(w):
    out = w.status(health(storage="not_configured"))
    assert "recording" not in by_id(out)
    assert out["verdict"] == "partial"                          # never "ok" with a gap


def test_a_stale_probe_is_left_out_not_shown_as_operational(w):
    out = w.status(health(streaming="unmonitored"))
    assert "live_streaming" not in by_id(out)
    assert out["verdict"] == "partial"


def test_nothing_measurable_is_unavailable_not_green(w):
    out = w.status({"services": []})
    assert out == {"verdict": "unavailable", "services": [], "platform_notice": None}


@pytest.mark.parametrize("probe, expected", [("warn", "warn"), ("down", "down")])
def test_a_probe_failure_shows_on_its_service(w, probe, expected):
    out = w.status(health(streaming=probe))
    assert by_id(out)["live_streaming"]["status"] == expected
    assert out["verdict"] == expected


# ── incidents ───────────────────────────────────────────────────────────────────────────

def test_open_incidents_escalate_their_service(w):
    w.incident("deliver", "sev3", title="Viewers rebuffering")
    w.incident("preserve", "sev1", title="Replays unavailable")
    status = w.status()
    out = by_id(status)
    assert out["live_streaming"]["status"] == "warn"
    # The internal title stays internal; the customer reads neutral wording.
    assert out["live_streaming"]["incident"] == {"title": "Degraded performance under investigation",
                                                 "severity": "minor"}
    assert "Viewers rebuffering" not in json.dumps(status) and "Replays unavailable" not in json.dumps(status)
    assert out["recording"]["status"] == "down"
    assert out["recording"]["incident"]["severity"] == "major"
    assert out["platform_api"]["status"] == "ok"


def test_a_platform_wide_incident_applies_to_every_organization(w):
    w.incident("platform", "sev2", scope="global")
    assert by_id(w.status())["platform_api"]["status"] == "down"


def test_another_organizations_incident_never_reaches_this_one(w, other):
    other.incident("deliver", "sev1")
    other.incident("preserve", "sev1", started=datetime.now(UTC) - 3 * H, resolved=datetime.now(UTC) - 2 * H)
    out = by_id(w.status())
    assert out["live_streaming"]["status"] == "ok" and out["live_streaming"]["incidents_24h"] == 0
    assert out["recording"]["incidents_24h"] == 0
    # The internal lifecycle had the same leak (ops.stage_health / ops.availability).
    stages = w.lifecycle()
    assert stages["deliver"]["status"] == "ok"
    assert stages["deliver"]["availability"] == 100.0 and stages["preserve"]["availability"] == 100.0


def test_incidents_24h_counts_recorded_incidents_in_the_window(w):
    now = datetime.now(UTC)
    w.incident("contribute", started=now - 3 * H, resolved=now - 2 * H)    # inside the window
    w.incident("ingest")                                                   # still open
    w.incident("deliver", started=now - 72 * H, resolved=now - 70 * H)     # long gone
    row = by_id(w.status())["live_streaming"]
    assert row["incidents_24h"] == 2 and row["open_incidents"] == 1


def test_an_incident_speaks_for_a_service_nothing_can_probe(w):
    w.incident("preserve", "sev3")
    out = by_id(w.status(health(storage="not_configured")))
    assert out["recording"]["status"] == "warn"


# ── recordings: LiveKit Egress has no probe, so recent starts are the evidence ─────────

def test_a_refused_recording_start_degrades_recording(w):
    w.recording_start(ok=False)
    assert by_id(w.status())["recording"]["status"] == "warn"


def test_two_refused_starts_in_a_row_are_an_outage(w):
    w.recording_start(ok=False, ago=timedelta(minutes=30))
    w.recording_start(ok=False, ago=timedelta(minutes=5))
    out = w.status()
    assert by_id(out)["recording"]["status"] == "down" and out["verdict"] == "down"


def test_a_successful_start_after_failures_reads_healthy(w):
    w.recording_start(ok=False, ago=timedelta(minutes=30))
    w.recording_start(ok=False, ago=timedelta(minutes=20))
    w.recording_start(ok=True, ago=timedelta(minutes=5))
    assert by_id(w.status())["recording"]["status"] == "ok"


def test_old_failures_and_other_organizations_failures_are_not_evidence(w, other):
    w.recording_start(ok=False, ago=timedelta(hours=30))
    other.recording_start(ok=False)
    other.recording_start(ok=False)
    # Nothing of its own in the window: no fault, and no claim either way.
    assert by_id(w.status())["recording"]["status"] == "idle"


def test_failures_speak_even_when_storage_cannot_be_probed_but_success_cannot(w):
    w.recording_start(ok=False)
    assert by_id(w.status(health(storage="unmonitored")))["recording"]["status"] == "warn"


def test_a_success_alone_never_makes_an_unprobed_recording_row_green(other):
    other.recording_start(ok=True)
    assert "recording" not in by_id(other.status(health(storage="unmonitored")))


# ── through the real route ──────────────────────────────────────────────────────────────

def test_the_overview_route_carries_service_status(w, monkeypatch):
    monkeypatch.setattr(admin_svc, "platform_health", lambda db: health(storage="not_configured"))
    db = SessionLocal()
    try:
        token = create_access_token(db.get(User, w.admin), remember=False, db=db)
    finally:
        db.close()
    c = TestClient(m.app)
    c.headers["Authorization"] = f"Bearer {token}"
    r = c.get("/api/organization/overview", params={"range": "24h"})
    assert r.status_code == 200, r.text
    status = r.json()["service_status"]
    assert status["verdict"] == "partial"
    assert [s["name"] for s in status["services"]] == ["Live streaming", "Management API"]
    for term in INTERNAL_TERMS:
        assert term not in json.dumps(status)

# ── recording & replay claims only what evidence supports ───────────────────────────────

def test_healthy_storage_alone_never_claims_recording_works(w):
    out = w.status()
    row = by_id(out)["recording"]
    assert row["status"] == "idle"                       # neither Operational nor a fault
    assert out["verdict"] == "ok"                        # an idle row faults nothing
    for term in INTERNAL_TERMS:
        assert term not in json.dumps(out)


def test_an_accepted_recording_start_confirms_recording(w):
    w.recording_start(ok=True)
    assert by_id(w.status())["recording"]["status"] == "ok"


def test_a_dual_recording_refusal_is_one_attempt_not_an_outage(w):
    at = datetime.now(UTC) - timedelta(minutes=3)
    w.recording_start(ok=False, at=at)                   # R2 and R3 of ONE start: same instant
    w.recording_start(ok=False, at=at)
    assert by_id(w.status())["recording"]["status"] == "warn"


def test_storage_failure_still_wins_over_an_accepted_start(w):
    w.recording_start(ok=True)
    assert by_id(w.status(health(storage="down")))["recording"]["status"] == "down"


# ── incidents that name no service ──────────────────────────────────────────────────────

def test_a_platform_wide_incident_with_no_stage_sets_the_verdict_without_claiming_a_service(w):
    w.recording_start(ok=True)
    w.incident(None, "sev2", scope="global", title="egress pool 3 OOM on node zs-eu-7")
    out = w.status()
    assert out["verdict"] == "down"
    assert out["platform_notice"] == {"title": "Service disruption under investigation", "severity": "major",
                                      "scope": "platform", "count": 1}
    assert all(s["status"] == "ok" and s["incident"] is None for s in out["services"])   # nothing guessed
    assert "OOM" not in json.dumps(out) and "zs-eu-7" not in json.dumps(out)


def test_a_minor_stageless_incident_reads_as_degraded(w):
    w.incident(None, "sev3", scope="global")
    out = w.status()
    assert out["verdict"] == "warn" and out["platform_notice"]["severity"] == "minor"


def test_an_organization_scoped_incident_reaches_only_that_organization(w, other):
    w.incident(None, "sev1", scope="org")
    assert w.status()["platform_notice"]["scope"] == "organization"
    theirs = other.status()
    assert theirs["platform_notice"] is None and theirs["verdict"] != "down"


def test_a_resolved_stageless_incident_is_no_longer_a_notice(w):
    now = datetime.now(UTC)
    w.incident(None, "sev1", scope="global", started=now - 3 * H, resolved=now - 2 * H)
    assert w.status()["platform_notice"] is None


@pytest.mark.parametrize("kind", ["security", "governance"])
def test_security_and_governance_incidents_never_change_service_health(w, kind):
    w.recording_start(ok=True)
    w.incident("deliver", "sev1", scope="global", kind=kind, title="credential stuffing on login")
    w.incident(None, "sev1", scope="org", kind=kind)
    out = w.status()
    assert out["verdict"] == "ok" and out["platform_notice"] is None
    assert by_id(out)["live_streaming"]["incidents_24h"] == 0
    assert "credential stuffing" not in json.dumps(out)


def test_the_admin_api_can_attribute_an_incident_to_a_stage_and_refuses_unknown_ones(w):
    from app.schemas.admin import IncidentCreate
    assert IncidentCreate(title="t", kind="operational", stage="deliver").stage == "deliver"
    assert IncidentCreate(title="t").stage is None
    with pytest.raises(ValueError):
        IncidentCreate(title="t", stage="transcoding")


# ── published public-status incidents ───────────────────────────────────────────────────

def test_a_published_incident_escalates_the_services_it_names_with_its_public_title(w):
    w.recording_start(ok=True)
    w.publish("Replays are failing to load", "major_outage", ["replay"])
    out = w.status()
    rows = by_id(out)
    assert rows["recording"]["status"] == "down"
    assert rows["recording"]["incident"] == {"title": "Replays are failing to load", "severity": "major"}
    assert rows["recording"]["open_incidents"] == 1 and rows["recording"]["incidents_24h"] == 1
    assert rows["live_streaming"]["status"] == "ok" and rows["platform_api"]["status"] == "ok"
    assert out["verdict"] == "down"


def test_a_published_incident_reaches_every_organization(w, other):
    w.publish("Viewers may see buffering", "degraded", ["live_streaming"])
    assert by_id(w.status())["live_streaming"]["status"] == "warn"
    assert by_id(other.status())["live_streaming"]["status"] == "warn"


def test_unpublished_resolved_and_no_impact_incidents_change_nothing(w):
    w.recording_start(ok=True)
    w.publish("draft", "major_outage", ["live_streaming"], published=False)
    w.publish("over", "major_outage", ["live_streaming"], resolved=True)
    w.publish("fyi", "none", ["live_streaming"])
    out = w.status()
    assert by_id(out)["live_streaming"]["status"] == "ok"
    assert out["platform_notice"] is None and out["verdict"] == "ok"


def test_a_published_incident_on_a_component_no_row_covers_is_a_platform_notice(w):
    w.publish("Invoices are delayed", "partial_outage", ["billing"])
    out = w.status()
    assert out["platform_notice"] == {"title": "Invoices are delayed", "severity": "minor",
                                      "scope": "platform", "count": 1}
    assert out["verdict"] == "warn"


# ── a failure here never takes the overview down ────────────────────────────────────────

def _overview(w):
    db = SessionLocal()
    try:
        token = create_access_token(db.get(User, w.admin), remember=False, db=db)
    finally:
        db.close()
    c = TestClient(m.app, raise_server_exceptions=False)
    c.headers["Authorization"] = f"Bearer {token}"
    return c.get("/api/organization/overview", params={"range": "24h"})


def _boom(*a, **k):
    raise RuntimeError("relation \"secret_internal_table\" does not exist")


@pytest.mark.parametrize("part, key, other_key", [
    ("customer_status", "service_status", "maintenance"),
    ("maintenance_schedule", "maintenance", "service_status"),
])
def test_a_failing_status_part_returns_the_rest_of_the_overview(w, monkeypatch, caplog, part, key, other_key):
    monkeypatch.setattr(org_svc, part, _boom)
    with caplog.at_level("ERROR"):
        r = _overview(w)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body[key] is None                                  # the page renders "unavailable"
    assert body[other_key] is not None                        # the sibling still answered
    for core in ("organization", "entitlements", "sessions", "upcoming_events"):
        assert core in body                                   # core organization data intact
    assert body["organization"]["id"] == str(w.org)
    assert "secret_internal_table" not in r.text and "Traceback" not in r.text
    assert any(part.replace("_", " ").split()[0] in rec.getMessage() or "failed" in rec.getMessage()
               for rec in caplog.records)

def test_a_real_sql_failure_in_the_status_part_leaves_the_transaction_usable(w, monkeypatch):
    # Postgres aborts the whole transaction on an error; without the savepoint every query the
    # overview makes after this one would fail too ("current transaction is aborted").
    from sqlalchemy import text

    def broken(db, *a, **k):
        db.execute(text("SELECT * FROM secret_internal_table"))

    monkeypatch.setattr(org_svc, "customer_status", broken)
    r = _overview(w)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["service_status"] is None and body["maintenance"] is not None
    assert body["developer_ops"] is not None and body["security_support"] is not None
    assert "secret_internal_table" not in r.text
