"""Administering a platform account is governance, not tenant support access.

── WHAT CHANGED, AND WHY IT IS NOT A RELAXATION ────────────────────────────────────────
PATCH /admin/users/{id} used to require an ORG-009 support context: the customer had to
approve a support session before a super admin could touch any field on the account. That
made ordinary platform administration impossible — fixing a typo in a name, or granting
someone the super_admin role, required asking a tenant for permission.

Every field UserUpdate carries is a PLATFORM attribute:

    role                   who administers the whole platform
    is_active              whether the account may authenticate at all
    staff_commercial_role  Zoiko's OWN staff scoping
    full_name              the account's display name

Asking a customer to approve who becomes a platform operator inverts the relationship: they
have no standing to grant it and no way to judge it. It is the same carve-out
services/tenant_access.py already documents for adverse platform enforcement — requiring the
customer's consent to act on the customer makes the control meaningless.

WHAT REPLACED IT, and what these pin:
  * super_admin is still required, at the router;
  * the HIGH-RISK subset (minting/removing a platform operator, switching one off) needs an
    active "identity" elevation — a short, expiring, audited step-up;
  * the active super-admin set can still never be emptied;
  * every change is audited, and refusals are audited too;
  * ORG-003 still ANNOUNCES the change to the organization. The posture moved from
    "customer pre-approves" to "customer is told", not to "nobody knows";
  * DELETE /admin/users/{id} is untouched and still ORG-009-gated, because destroying a
    tenant's member record is not governance of an account.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import Organization, User
from app.models.platform_ops import ElevationSession
from app.security import create_access_token, hash_password

UTC = timezone.utc


@pytest.fixture
def world():
    db = SessionLocal()
    made = {"u": [], "o": [], "e": []}
    try:
        org = Organization(name=f"GovCo {uuid.uuid4().hex[:6]}", status="active")
        db.add(org)
        db.flush()
        made["o"] = [org.id]

        def mk(role, name):
            u = User(org_id=org.id, full_name=name, role=role, is_active=True,
                     email=f"{role}-{uuid.uuid4().hex[:10]}@example.com",
                     username=f"{role[:4]}{uuid.uuid4().hex[:10]}",
                     password_hash=hash_password("x"), email_verified=True)
            db.add(u)
            db.flush()
            made["u"].append(u.id)
            return u

        people = {
            "admin": mk("super_admin", "Acting Admin"),
            "member": mk("org_admin", "Tenant Member"),
            "host": mk("host", "A Host"),
        }
        db.commit()
        yield {"db": db, "org": org, "made": made, **people}
    finally:
        try:
            db.execute(ElevationSession.__table__.delete().where(
                ElevationSession.user_id.in_(made["u"])))
            for uid in made["u"]:
                db.execute(User.__table__.delete().where(User.id == uid))
            for oid in made["o"]:
                db.execute(Organization.__table__.delete().where(Organization.id == oid))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


def client_for(user):
    c = TestClient(m.app)
    c.headers.update({"Authorization": f"Bearer {create_access_token(user, remember=False)}"})
    return c


def elevate(world, scope="identity", minutes=15):
    now = datetime.now(UTC)
    row = ElevationSession(user_id=world["admin"].id, scope=scope, scopes=[],
                           reason="governance test", granted_at=now,
                           expires_at=now + timedelta(minutes=minutes))
    world["db"].add(row)
    world["db"].commit()
    world["made"]["e"].append(row.id)
    return row


# ── ordinary administration needs no tenant approval ───────────────────────────────────

def test_renaming_an_account_no_longer_needs_organization_approval(world):
    """THE REPORTED BUG. This returned 403 "This Organization has not approved an active
    support session" for a name edit."""
    c = client_for(world["admin"])

    r = c.patch(f"/api/admin/users/{world['member'].id}", json={"full_name": "Corrected Name"})

    assert r.status_code == 200, r.text
    world["db"].expire_all()
    assert world["db"].get(User, world["member"].id).full_name == "Corrected Name"


def test_deactivating_an_ordinary_member_needs_no_tenant_approval(world):
    c = client_for(world["admin"])

    r = c.patch(f"/api/admin/users/{world['member'].id}", json={"is_active": False})

    assert r.status_code == 200, r.text
    world["db"].expire_all()
    assert world["db"].get(User, world["member"].id).is_active is False


def test_the_refusal_text_is_gone(world):
    c = client_for(world["admin"])
    r = c.patch(f"/api/admin/users/{world['member'].id}", json={"full_name": "X"})
    assert "has not approved" not in r.text
    assert "support session" not in r.text.lower()


# ── the high-risk subset still needs a deliberate step-up ──────────────────────────────

def test_granting_super_admin_without_elevation_is_refused(world):
    """Minting a platform operator is not ordinary administration."""
    c = client_for(world["admin"])

    r = c.patch(f"/api/admin/users/{world['member'].id}", json={"role": "super_admin"})

    assert r.status_code == 403, r.text
    assert "elevation" in r.json()["detail"].lower()
    world["db"].expire_all()
    assert world["db"].get(User, world["member"].id).role == "org_admin", "and nothing changed"


def test_granting_super_admin_with_elevation_is_allowed(world):
    elevate(world)
    c = client_for(world["admin"])

    r = c.patch(f"/api/admin/users/{world['member'].id}", json={"role": "super_admin"})

    assert r.status_code == 200, r.text
    world["db"].expire_all()
    assert world["db"].get(User, world["member"].id).role == "super_admin"


def test_removing_super_admin_also_needs_elevation(world):
    """Both directions. Taking the role away is as consequential as granting it."""
    world["member"].role = "super_admin"
    world["db"].commit()
    c = client_for(world["admin"])

    r = c.patch(f"/api/admin/users/{world['member'].id}", json={"role": "org_admin"})

    assert r.status_code == 403
    assert "elevation" in r.json()["detail"].lower()


def test_a_refused_high_risk_change_is_audited(world):
    """A refused attempt to mint a platform operator is worth knowing about."""
    from app.models import AuditLog

    c = client_for(world["admin"])
    c.patch(f"/api/admin/users/{world['member'].id}", json={"role": "super_admin"})

    world["db"].expire_all()
    rows = world["db"].query(AuditLog).filter(
        AuditLog.action == "user.update.denied_no_elevation",
        AuditLog.target_id == str(world["member"].id),
    ).all()
    assert rows, "the refusal must leave a record"


def test_an_ordinary_change_does_not_demand_elevation(world):
    """The step-up must stay proportionate: requiring it for everything is the same as not
    having it, because operators would hold one permanently."""
    c = client_for(world["admin"])
    assert c.patch(f"/api/admin/users/{world['member'].id}",
                   json={"full_name": "Ordinary Edit"}).status_code == 200


# ── every other safeguard is untouched ─────────────────────────────────────────────────

def test_the_last_active_super_admin_still_cannot_be_demoted(world):
    """Checked BEFORE the elevation gate, so holding an elevation does not buy a way around
    it — the platform must never be left with zero administrators."""
    from fastapi import HTTPException

    from app.routers.admin import _assert_super_admins_remain

    db = world["db"]
    others = db.query(User).filter(
        User.role == "super_admin", User.is_active.is_(True), User.id != world["admin"].id
    ).count()
    if others:
        _assert_super_admins_remain(db, world["admin"], demoting_to="org_admin")
    else:
        with pytest.raises(HTTPException) as exc:
            _assert_super_admins_remain(db, world["admin"], demoting_to="org_admin")
        assert exc.value.status_code == 409
    db.rollback()


def test_an_admin_still_cannot_demote_or_deactivate_themselves(world):
    c = client_for(world["admin"])
    assert c.patch(f"/api/admin/users/{world['admin'].id}",
                   json={"is_active": False}).status_code == 400


def test_a_non_super_admin_cannot_use_the_endpoint_at_all(world):
    """The role check runs at the router and is not negotiable."""
    c = client_for(world["host"])
    r = c.patch(f"/api/admin/users/{world['member'].id}", json={"full_name": "Nope"})
    assert r.status_code == 403
    assert "super admin" in r.json()["detail"].lower()


def test_an_invalid_role_is_rejected(world):
    elevate(world)
    c = client_for(world["admin"])
    r = c.patch(f"/api/admin/users/{world['member'].id}", json={"role": "wizard"})
    assert r.status_code in (400, 422), r.text
    world["db"].expire_all()
    assert world["db"].get(User, world["member"].id).role == "org_admin"


def test_a_successful_change_is_audited_with_the_acting_admin(world):
    from app.models import AuditLog

    c = client_for(world["admin"])
    assert c.patch(f"/api/admin/users/{world['member'].id}",
                   json={"full_name": "Audited Name"}).status_code == 200

    world["db"].expire_all()
    rows = world["db"].query(AuditLog).filter(
        AuditLog.action == "user.update", AuditLog.target_id == str(world["member"].id)
    ).all()
    assert rows, "a privileged mutation must be audited"
    assert any(r.actor_id == world["admin"].id for r in rows), "attributed to the acting admin"


# ── tenant support access is still a separate, gated thing ─────────────────────────────

def test_deleting_a_member_still_requires_organization_approval(world):
    """The line held. Destroying a tenant's member record is not governance of a platform
    account — it removes customer data, so ORG-009 still applies there."""
    c = client_for(world["admin"])

    r = c.delete(f"/api/admin/users/{world['member'].id}")

    assert r.status_code == 403, r.text
    assert "approved" in r.text.lower() or "support" in r.text.lower()
    world["db"].expire_all()
    assert world["db"].get(User, world["member"].id) is not None, "and the account survives"
