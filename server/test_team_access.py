"""GET /organization/team/access — event-scoped role grants for ORG-06.

The grant is the EventAssignment row itself (see test_self_host_assignment.py), so the count
is rows on the organization's events that are not deleted; each member's scope is their
distinct events and roles. Another organization's grants are never counted, and only an
organization admin may read it.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import ratelimit
from app.db import SessionLocal
from app.main import app
from app.models import Event, EventAssignment, Organization, User
from app.security import hash_password

client = TestClient(app)
PASSWORD = "correct-horse-battery"


class Org:
    def __init__(self, label):
        db = SessionLocal()
        try:
            org = Organization(name=f"{label} {uuid.uuid4().hex[:6]}", status="active")
            db.add(org)
            db.flush()
            self.id = org.id
            self.admin_email, self.admin_id = self._member(db, "org_admin")
            self.host_email, self.host_id = self._member(db, "host")
            self.speaker_email, self.speaker_id = self._member(db, "speaker")
            db.commit()
        finally:
            db.close()

    def _member(self, db, role):
        email = f"{role}-{uuid.uuid4().hex[:10]}@example.com"
        user = User(org_id=self.id, full_name=role.title(), role=role, is_active=True, email=email,
                    username=f"u{uuid.uuid4().hex[:10]}", password_hash=hash_password(PASSWORD),
                    email_verified=True)
        db.add(user)
        db.flush()
        return email, user.id

    def event(self, *assignments, deleted=False):
        db = SessionLocal()
        try:
            ev = Event(org_id=self.id, created_by=self.admin_id, title=f"E {uuid.uuid4().hex[:6]}",
                       status="draft", deleted_at=datetime.now(timezone.utc) if deleted else None)
            db.add(ev)
            db.flush()
            for user_id, role in assignments:
                db.add(EventAssignment(event_id=ev.id, user_id=user_id, role=role))
            db.commit()
            return ev.id
        finally:
            db.close()

    def get(self, email=None):
        ratelimit._HITS.clear()
        r = client.post("/api/auth/login", json={"identifier": email or self.admin_email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return client.get("/api/organization/team/access",
                          headers={"Authorization": f"Bearer {r.json()['access_token']}"})

    def cleanup(self):
        db = SessionLocal()
        try:
            for ev in db.scalars(select(Event).where(Event.org_id == self.id)).all():
                db.query(EventAssignment).filter(EventAssignment.event_id == ev.id).delete()
                db.delete(ev)
            db.commit()
            db.query(User).filter(User.org_id == self.id).delete()
            db.commit()
            org = db.get(Organization, self.id)
            if org is not None:
                db.delete(org)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


@pytest.fixture
def org():
    o = Org("GrantCo")
    try:
        yield o
    finally:
        o.cleanup()


@pytest.fixture
def other():
    o = Org("ElsewhereCo")
    try:
        yield o
    finally:
        o.cleanup()


def test_counts_each_grant_and_each_members_scope(org):
    org.event((org.host_id, "host"), (org.speaker_id, "speaker"))
    org.event((org.host_id, "host"))
    org.event((org.host_id, "speaker"))
    r = org.get()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grants"] == 4
    assert body["members"][str(org.host_id)] == {"events": 3, "roles": {"host": 2, "speaker": 1}}
    assert body["members"][str(org.speaker_id)] == {"events": 1, "roles": {"speaker": 1}}
    assert str(org.admin_id) not in body["members"]       # no assignment, no entry


def test_a_deleted_events_grants_no_longer_count(org):
    org.event((org.host_id, "host"))
    org.event((org.host_id, "host"), (org.speaker_id, "speaker"), deleted=True)
    body = org.get().json()
    assert body["grants"] == 1
    assert body["members"] == {str(org.host_id): {"events": 1, "roles": {"host": 1}}}


def test_another_organizations_grants_are_never_counted(org, other):
    org.event((org.host_id, "host"))
    other.event((other.host_id, "host"), (other.speaker_id, "speaker"))
    assert org.get().json() == {"grants": 1, "members": {str(org.host_id): {"events": 1, "roles": {"host": 1}}}}
    assert other.get().json()["grants"] == 2


def test_only_an_organization_admin_may_read_it(org):
    org.event((org.host_id, "host"))
    assert org.get(org.host_email).status_code == 403
    assert client.get("/api/organization/team/access").status_code == 401
