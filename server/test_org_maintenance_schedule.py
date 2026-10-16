"""/organization/support's Scheduled maintenance panel: published windows, honestly.

The panel used to read a hard-coded `security_support.maintenance_window = None` and tell
customers "No maintenance calendar is connected. This deployment has no maintenance
scheduler…". The platform does have a maintenance record — `scheduled_maintenances`, written by
status_publication.schedule_maintenance and announced on the public status page — so the
overview now returns `maintenance`: the published windows still ahead or under way (the exact
set the public page lists) and `authoritative`, which stays False while no operator tool can
publish a window, so an EMPTY schedule is never presented as "no maintenance planned".
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import AuditLog, Organization, ScheduledMaintenance, User
from app.security import create_access_token, hash_password
from app.services import bus
from app.services import org as org_svc
from app.services import status_publication as sp

UTC = timezone.utc
H = timedelta(hours=1)


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")


class World:
    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Maint {uuid.uuid4().hex[:6]}", status="active")
            db.add(org)
            db.flush()
            admin = User(org_id=org.id, full_name="Maint Admin", role="org_admin", is_active=True,
                         email=f"mt-{uuid.uuid4().hex[:10]}@example.com", username=f"mt{uuid.uuid4().hex[:10]}",
                         password_hash=hash_password("x"), email_verified=True)
            db.add(admin)
            db.commit()
            self.org, self.admin = org.id, admin.id
            self.windows = []
        finally:
            db.close()

    def publish(self, title, *, hours_out=48, length=2, kind="scheduled", reason=None,
                components=("live_streaming", "recording"), regions=("eu",)):
        db = SessionLocal()
        try:
            row = sp.schedule_maintenance(
                db, title=title, components=list(components), regions=list(regions),
                starts_at_utc=datetime.now(UTC) + hours_out * H,
                ends_at_utc=datetime.now(UTC) + (hours_out + length) * H,
                impact_summary="Live sessions may briefly reconnect.", kind=kind, emergency_reason=reason)
            self.windows.append(row.id)
            return row.id
        finally:
            db.close()

    def set(self, window_id, **fields):
        db = SessionLocal()
        try:
            row = db.get(ScheduledMaintenance, window_id)
            for k, v in fields.items():
                setattr(row, k, v)
            db.commit()
        finally:
            db.close()

    def overview(self):
        db = SessionLocal()
        try:
            token = create_access_token(db.get(User, self.admin), remember=False, db=db)
        finally:
            db.close()
        c = TestClient(m.app)
        c.headers["Authorization"] = f"Bearer {token}"
        r = c.get("/api/organization/overview", params={"range": "24h"})
        assert r.status_code == 200, r.text
        return r.json()

    def cleanup(self):
        db = SessionLocal()
        try:
            if self.windows:
                db.query(ScheduledMaintenance).filter(ScheduledMaintenance.id.in_(self.windows)).delete(
                    synchronize_session=False)
            db.query(AuditLog).filter(AuditLog.org_id == self.org).delete(synchronize_session=False)
            db.query(User).filter(User.id == self.admin).delete(synchronize_session=False)
            db.query(Organization).filter(Organization.id == self.org).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


@pytest.fixture
def w():
    world = World()
    try:
        yield world
    finally:
        world.cleanup()


def mine(w, maintenance):
    titles = {f"{w.org}-a", f"{w.org}-b", f"{w.org}-emergency", f"{w.org}-draft", f"{w.org}-done"}
    return [x for x in maintenance["upcoming"] if x["title"] in titles]


def test_a_published_window_reaches_the_support_page_in_customer_terms(w):
    w.publish(f"{w.org}-a")
    out = w.overview()["maintenance"]
    (window,) = mine(w, out)
    assert window["status"] == "scheduled" and window["kind"] == "scheduled"
    assert window["components"] == ["Live streaming", "Recording"]
    assert window["regions"] == ["Europe"]
    assert window["impact"] == "Live sessions may briefly reconnect."
    start, end = datetime.fromisoformat(window["starts_at"]), datetime.fromisoformat(window["ends_at"])
    assert start.utcoffset() == timedelta(0) and end - start == 2 * H     # UTC instants
    assert "id" not in window                                            # no internal id


def test_only_published_upcoming_windows_soonest_first(w):
    later, sooner = w.publish(f"{w.org}-b", hours_out=72), w.publish(f"{w.org}-a", hours_out=24)
    draft = w.publish(f"{w.org}-draft")
    w.set(draft, published_at=None)                          # never published
    done = w.publish(f"{w.org}-done")
    w.set(done, status="completed", completed_at=datetime.now(UTC))
    titles = [x["title"] for x in mine(w, w.overview()["maintenance"])]
    assert titles == [f"{w.org}-a", f"{w.org}-b"]
    assert sooner and later


def test_emergency_work_is_never_presented_as_scheduled(w):
    w.publish(f"{w.org}-emergency", kind="emergency", reason="stability")
    (window,) = mine(w, w.overview()["maintenance"])
    assert window["kind"] == "emergency"


def test_the_support_page_and_the_public_status_page_list_the_same_windows(w):
    w.publish(f"{w.org}-a")
    w.publish(f"{w.org}-b", hours_out=96)
    support = {x["reference"] for x in w.overview()["maintenance"]["upcoming"]}
    public = {x["reference"] for x in TestClient(m.app).get("/api/status").json()["scheduled_maintenance"]}
    assert support == public


def test_an_empty_schedule_is_not_claimed_to_be_authoritative(w):
    out = w.overview()["maintenance"]
    # No operator tool publishes windows yet, so "nothing published" is not "nothing planned".
    assert out["authoritative"] is False
    assert org_svc.MAINTENANCE_SCHEDULE_AUTHORITATIVE is False


def test_no_internal_wording_in_the_maintenance_payload(w):
    w.publish(f"{w.org}-a")
    dumped = json.dumps(w.overview()["maintenance"]).lower()
    for phrase in ("calendar", "scheduler", "deployment", "not connected", "not configured", "integrated"):
        assert phrase not in dumped
