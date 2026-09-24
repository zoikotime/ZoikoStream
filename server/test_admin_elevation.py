"""Elevation is a real gate now, not a badge.

── WHAT CHANGED ────────────────────────────────────────────────────────────────────────
The console has always shown "Standing access / Elevate" with a live countdown, which tells
an operator that destructive platform actions are behind a short, deliberate, audited
step-up. Nothing enforced it: POST /admin/elevation opened a session and
`services/ops.current_elevation` was read only to DRAW that badge. A super admin could
delete an account, flip a platform setting or force-end a broadcast with the badge dark.

`security.require_elevation(scope)` now gates the high-risk endpoints. These pin the four
things that make it a control rather than a decoration: absent is denied, expired is denied,
wrong scope is denied, and reads are untouched.

DEPTH, NOT BREADTH: elevation is layered on top of require_super_admin and grants nothing
inside a customer tenant — reaching into an Organization still goes through
/admin/support-access with that org's approval and countersign (ORG-009). The last test
here pins that separation.
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
        org = Organization(name=f"ElevCo {uuid.uuid4().hex[:6]}", status="active")
        db.add(org)
        db.flush()
        made["o"] = [org.id]

        def mk(role):
            u = User(org_id=org.id, full_name=f"{role} user", role=role, is_active=True,
                     email=f"{role}-{uuid.uuid4().hex[:10]}@example.com",
                     username=f"{role}{uuid.uuid4().hex[:10]}",
                     password_hash=hash_password("x"), email_verified=True)
            db.add(u)
            db.flush()
            made["u"].append(u.id)
            return u

        admin = mk("super_admin")
        victim = mk("viewer")
        db.commit()
        yield {"db": db, "org": org, "admin": admin, "victim": victim, "made": made}
    finally:
        try:
            for eid in made["e"]:
                db.execute(ElevationSession.__table__.delete().where(ElevationSession.id == eid))
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


def elevate(world, scope="identity", minutes=15, scopes=None, expired=False):
    """Insert an elevation session directly, so expiry can be controlled exactly."""
    db = world["db"]
    now = datetime.now(UTC)
    row = ElevationSession(
        user_id=world["admin"].id, scope=scope, scopes=scopes or [],
        reason="audit test",
        granted_at=now - timedelta(minutes=60),
        expires_at=now - timedelta(minutes=1) if expired else now + timedelta(minutes=minutes),
    )
    db.add(row)
    db.commit()
    world["made"]["e"].append(row.id)
    return row


# ── the gate ───────────────────────────────────────────────────────────────────────────

def test_without_elevation_a_destructive_action_is_denied(world):
    c = client_for(world["admin"])

    r = c.patch("/api/admin/settings", json={"values": {}})

    assert r.status_code == 403, r.text
    assert "elevat" in r.json()["detail"].lower()


def test_with_a_valid_in_scope_elevation_it_is_allowed(world):
    """Positive path on a PLATFORM-scope action.

    Deliberately not a user mutation: /admin/users/{id} is ALSO behind the ORG-009 support
    context, so it would 403 on tenant approval even with a perfect elevation — which is
    correct, and is pinned separately in
    test_user_mutation_needs_elevation_AND_tenant_approval below. Proving "elevation lets it
    through" needs an endpoint where elevation is the only gate."""
    elevate(world, scope="platform")
    c = client_for(world["admin"])

    r = c.patch("/api/admin/settings", json={"values": {}})

    assert r.status_code == 200, r.text


def test_an_expired_elevation_is_denied(world):
    # The badge may still be on screen; the server is what decides.
    elevate(world, scope="platform", expired=True)
    c = client_for(world["admin"])

    r = c.patch("/api/admin/settings", json={"values": {}})

    assert r.status_code == 403, r.text


def test_the_wrong_scope_is_denied(world):
    # A broadcast elevation must not authorize a platform action.
    elevate(world, scope="broadcast")
    c = client_for(world["admin"])

    r = c.patch("/api/admin/settings", json={"values": {}})

    assert r.status_code == 403, r.text
    assert "platform" in r.json()["detail"]


def test_the_secondary_scopes_list_also_authorizes(world):
    # One elevation can legitimately cover several related actions: the headline `scope` is
    # "broadcast" here, and "platform" rides along in `scopes`.
    elevate(world, scope="broadcast", scopes=["platform"])
    c = client_for(world["admin"])

    r = c.patch("/api/admin/settings", json={"values": {}})

    assert r.status_code == 200, r.text


def test_tenant_actions_are_gated_by_ORG_009_not_by_elevation(world):
    """The division of labour, pinned.

    Reaching into a customer's organization is NOT an elevation question — /admin/users/{id}
    sits behind the support context, which is org-approved, countersigned, capability-scoped
    and audits its own refusals. Stacking elevation in front of it pre-empted that check and
    silenced `support_access.denied_out_of_scope`, so the coarser gate was removed from these
    two endpoints. What must stay true is that the tenant control still refuses on its own."""
    elevate(world, scope="identity")     # a platform elevation buys nothing here
    c = client_for(world["admin"])

    r = c.patch(f"/api/admin/users/{world['victim'].id}", json={"is_active": False})

    assert r.status_code == 403, r.text
    assert "support" in r.json()["detail"].lower()
    world["db"].expire_all()
    assert world["db"].get(User, world["victim"].id).is_active is True


# ── reads are untouched ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/admin/users", "/api/admin/organizations", "/api/admin/audit-logs",
    "/api/admin/platform-health", "/api/admin/roles", "/api/admin/feature-flags",
])
def test_read_only_admin_pages_need_no_elevation(world, path):
    """Browsing the console must not demand a step-up — that would make elevation constant,
    which is the same as not having it."""
    c = client_for(world["admin"])
    assert c.get(path).status_code == 200, path


# ── the guarded set ────────────────────────────────────────────────────────────────────

def test_platform_configuration_requires_elevation(world):
    c = client_for(world["admin"])
    assert c.patch("/api/admin/settings", json={"values": {}}).status_code == 403


def test_feature_flag_writes_require_elevation(world):
    c = client_for(world["admin"])
    r = c.post("/api/admin/feature-flags",
               json={"key": f"t_{uuid.uuid4().hex[:8]}", "description": "x", "enabled": False})
    assert r.status_code == 403, r.text


def test_organization_deletion_requires_elevation(world):
    c = client_for(world["admin"])
    assert c.delete(f"/api/admin/organizations/{world['org'].id}").status_code == 403
    # The org is still there.
    world["db"].expire_all()
    assert world["db"].get(Organization, world["org"].id) is not None


# ── it is DEPTH on top of role, never a replacement ────────────────────────────────────

def test_elevation_does_not_let_a_non_super_admin_in(world):
    """A viewer with an elevation row must still be refused — require_super_admin runs at the
    router and is not negotiable."""
    db = world["db"]
    row = ElevationSession(
        user_id=world["victim"].id, scope="identity", scopes=[], reason="should not help",
        granted_at=datetime.now(UTC), expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    db.add(row)
    db.commit()
    world["made"]["e"].append(row.id)

    c = client_for(world["victim"])
    r = c.patch("/api/admin/settings", json={"values": {}})

    assert r.status_code == 403
    assert "super admin" in r.json()["detail"].lower()


def test_elevation_carries_no_org_scope(world):
    """ORG-009 separation: elevation is platform-wide by construction — it has no org field,
    so it cannot silently become tenant access. Reaching into an Organization still goes
    through /admin/support-access with that org's own approval and countersign."""
    row = elevate(world, scope="platform")
    assert not hasattr(row, "org_id")


# ── the button and the gate must speak the same language ───────────────────────────────

def test_the_consoles_elevate_button_grants_scopes_the_gate_accepts():
    """A contract test across the language boundary, for the failure it actually had.

    The rail's Elevate control posted scope "Platform Operations" with scopes
    ["organizations:write", ...]. None of that is in ELEVATION_SCOPES, so the one button the
    console offers for stepping up produced a grant that satisfied no guard: the operator
    elevated, the badge lit, and every protected action still returned 403. Enforcement and
    the way to satisfy it were written in different vocabularies and nothing compared them.

    This reads the real payload out of the component rather than a copy of it, so renaming a
    scope on either side fails here instead of in production.
    """
    import re
    from pathlib import Path

    from app.security import ELEVATION_SCOPES

    src = (Path(__file__).resolve().parents[1]
           / "client" / "src" / "components" / "admin" / "AdminSidebar.jsx").read_text(encoding="utf-8")
    body = re.search(r'api\.post\("/admin/elevation",\s*\{(.*?)\n\s*\}\)', src, re.S)
    assert body, "could not find the Elevate button's request in AdminSidebar.jsx"
    payload = body.group(1)

    scope = re.search(r'\bscope:\s*"([^"]+)"', payload)
    assert scope, "the Elevate request sends no scope"
    extra = re.search(r'\bscopes:\s*\[([^\]]*)\]', payload)
    granted = {scope.group(1)} | set(re.findall(r'"([^"]+)"', extra.group(1) if extra else ""))

    unknown = granted - set(ELEVATION_SCOPES)
    assert not unknown, f"the console grants scopes the server does not enforce: {sorted(unknown)}"

    # And it must actually cover what the console gates, or the button unlocks nothing.
    assert {"platform", "broadcast", "identity"} <= granted, \
        f"the Elevate button does not cover the guarded console actions; it grants {sorted(granted)}"

    # "support" is NOT self-granted here: tenant reach is ORG-009's, via /admin/support-access.
    assert "support" not in granted


def test_a_console_shaped_grant_unlocks_a_guarded_endpoint(world):
    """End to end on the same vocabulary: the payload the button sends, then the action."""
    elevate(world, scope="platform", scopes=["identity", "broadcast"])
    c = client_for(world["admin"])

    assert c.patch("/api/admin/settings", json={"values": {}}).status_code == 200
