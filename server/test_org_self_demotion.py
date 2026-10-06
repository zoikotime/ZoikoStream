"""Tests for Organization user role-change and self-protection error contracts:
- Self-demotion (Admin -> Host) is rejected with 400 and SELF_ROLE_CHANGE_FORBIDDEN
- Self-deactivation is rejected with 400 and SELF_ROLE_CHANGE_FORBIDDEN
- Self-deletion is rejected with 400 and SELF_DELETE_FORBIDDEN
- Other member role change succeeds with 200
- Non-admin attempts return 403
- OperationalError classification differentiates genuine DB unreachable (503) vs transaction errors (500)
"""
import uuid
import pytest
from starlette.testclient import TestClient
from sqlalchemy.exc import OperationalError

import app.main as m
from app.db import Base, SessionLocal, engine
from app.models import Organization, User
from app.security import create_access_token, hash_password


@pytest.fixture
def test_setup():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    org_id = uuid.uuid4()
    org = Organization(
        id=org_id,
        name=f"Test Org {org_id.hex[:6]}",
        slug=f"test-org-{org_id.hex[:6]}",
        status="active",
    )
    db.add(org)
    db.commit()

    admin = User(
        id=uuid.uuid4(),
        email=f"admin-{org_id.hex[:6]}@example.com",
        username=f"admin_{org_id.hex[:6]}",
        full_name="Admin User",
        password_hash=hash_password("test-pass-123"),
        org_id=org_id,
        role="org_admin",
        is_active=True,
        email_verified=True,
    )
    member = User(
        id=uuid.uuid4(),
        email=f"member-{org_id.hex[:6]}@example.com",
        username=f"member_{org_id.hex[:6]}",
        full_name="Other Member",
        password_hash=hash_password("test-pass-123"),
        org_id=org_id,
        role="viewer",
        is_active=True,
        email_verified=True,
    )
    db.add_all([admin, member])
    db.commit()

    yield {
        "org_id": org_id,
        "admin": admin,
        "member": member,
    }

    # Teardown
    db.query(User).filter(User.org_id == org_id).delete()
    db.query(Organization).filter(Organization.id == org_id).delete()
    db.commit()
    db.close()


def test_admin_cannot_demote_self(test_setup):
    client = TestClient(m.app)
    admin = test_setup["admin"]
    token = create_access_token(admin, remember=False)
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt to demote self to host
    resp = client.patch(
        f"/api/organization/users/{admin.id}",
        json={"role": "host"},
        headers=headers,
    )

    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", {})
    assert isinstance(detail, dict)
    assert detail.get("code") == "SELF_ROLE_CHANGE_FORBIDDEN"
    assert "You cannot demote or deactivate yourself" in detail.get("message", "")

    # Verify admin role remains org_admin in DB
    db = SessionLocal()
    try:
        u = db.get(User, admin.id)
        assert u.role == "org_admin"
    finally:
        db.close()


def test_admin_cannot_deactivate_self(test_setup):
    client = TestClient(m.app)
    admin = test_setup["admin"]
    token = create_access_token(admin, remember=False)
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt to deactivate self
    resp = client.patch(
        f"/api/organization/users/{admin.id}",
        json={"is_active": False},
        headers=headers,
    )

    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", {})
    assert isinstance(detail, dict)
    assert detail.get("code") == "SELF_ROLE_CHANGE_FORBIDDEN"
    assert "You cannot demote or deactivate yourself" in detail.get("message", "")

    # Verify admin is still active
    db = SessionLocal()
    try:
        u = db.get(User, admin.id)
        assert u.is_active is True
    finally:
        db.close()


def test_admin_cannot_delete_self(test_setup):
    client = TestClient(m.app)
    admin = test_setup["admin"]
    token = create_access_token(admin, remember=False)
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt to delete self
    resp = client.delete(
        f"/api/organization/users/{admin.id}",
        headers=headers,
    )

    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", {})
    assert isinstance(detail, dict)
    assert detail.get("code") == "SELF_DELETE_FORBIDDEN"
    assert "You cannot delete your own account" in detail.get("message", "")


def test_admin_can_change_other_member_role(test_setup):
    client = TestClient(m.app)
    admin = test_setup["admin"]
    member = test_setup["member"]
    token = create_access_token(admin, remember=False)
    headers = {"Authorization": f"Bearer {token}"}

    # Change other member from viewer to host
    resp = client.patch(
        f"/api/organization/users/{member.id}",
        json={"role": "host"},
        headers=headers,
    )

    assert resp.status_code == 200
    assert resp.json()["role"] == "host"

    # Verify in DB
    db = SessionLocal()
    try:
        u = db.get(User, member.id)
        assert u.role == "host"
    finally:
        db.close()


def test_non_admin_cannot_change_roles(test_setup):
    client = TestClient(m.app)
    member = test_setup["member"]
    admin = test_setup["admin"]
    token = create_access_token(member, remember=False)
    headers = {"Authorization": f"Bearer {token}"}

    # Viewer tries to change admin's role
    resp = client.patch(
        f"/api/organization/users/{admin.id}",
        json={"role": "viewer"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_db_connectivity_failure_classification():
    # Genuine connectivity failure: connection refused
    exc_conn_refused = OperationalError("SELECT 1", {}, Exception("could not connect to server: Connection refused"))
    assert m.is_db_connectivity_failure(exc_conn_refused) is True

    # Genuine connectivity failure: timeout
    exc_timeout = OperationalError("SELECT 1", {}, Exception("connection timed out"))
    assert m.is_db_connectivity_failure(exc_timeout) is True

    # Genuine connectivity failure: queuepool exhausted
    exc_pool = OperationalError("SELECT 1", {}, Exception("QueuePool limit of size 10 overflow 10 reached"))
    assert m.is_db_connectivity_failure(exc_pool) is True

    # Genuine Postgres shutdown
    class MockOrigPg:
        pgcode = "57P01"
    exc_pg = OperationalError("SELECT 1", {}, MockOrigPg())
    assert m.is_db_connectivity_failure(exc_pg) is True

    # Non-connectivity error: e.g. transaction rollback or aborted block
    class MockOrigTx:
        pgcode = "25P02"  # in_failed_sql_transaction
    exc_tx = OperationalError("SELECT 1", {}, MockOrigTx())
    assert m.is_db_connectivity_failure(exc_tx) is False

    # Random lock failure: does NOT trigger DB unreachable
    exc_lock = OperationalError("SELECT 1", {}, Exception("could not obtain lock on row in relation 'users'"))
    assert m.is_db_connectivity_failure(exc_lock) is False
