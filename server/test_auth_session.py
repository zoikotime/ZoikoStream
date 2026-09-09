"""Server-side session validation.

The frontend route guard is UX. THIS is the boundary that matters: whatever a browser
believes about itself, a request without a valid credential must be refused.

The companion frontend suite (client/src/auth/AuthContext.test.jsx) proves the browser asks
this endpoint and honours its answer. This one proves the answer is trustworthy:

    no credential            -> 401
    garbage / forged token   -> 401
    token signed elsewhere   -> 401
    expired token            -> 401
    deactivated user         -> 401
    valid token              -> 200, and the caller's own record
    org_admin at /admin/*    -> 403

Run with `python test_auth_session.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from jose import jwt
from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.config import settings
from app.db import SessionLocal
from app.models import Organization, User
from app.security import ALGORITHM, create_access_token, hash_password

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"


class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


def _swallow(url, headers=None, json=None, timeout=None):
    """Signing in sends IDN-003. Not what this suite is about, so it goes nowhere."""
    return _Resp()


email_mod.httpx.post = _swallow


def _now():
    return datetime.now(timezone.utc)


def _new_email(tag="auth"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class World:
    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Auth Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="UTC")
            db.add(org)
            db.flush()
            self.org_id = org.id
            self.admin_email = _new_email("orgadmin")
            self.admin_id = self._u(db, "org_admin", self.admin_email)
            self.staff_email = _new_email("staff")
            self.staff_id = self._u(db, "super_admin", self.staff_email)
            self.disabled_email = _new_email("disabled")
            self.disabled_id = self._u(db, "org_admin", self.disabled_email)
            org.owner_user_id = self.admin_id
            db.commit()
        finally:
            db.close()

    def _u(self, db, role, email):
        user = User(org_id=self.org_id, full_name="Auth Person", role=role, is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=_now())
        db.add(user)
        db.flush()
        return user.id

    def login(self, email):
        """A real sign-in through the real endpoint. Returns the issued token."""
        client = TestClient(m.app)
        ratelimit._HITS.clear()
        r = client.post("/api/auth/login",
                        json={"identifier": email, "password": PASSWORD},
                        headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    def cleanup(self):
        db = SessionLocal()
        try:
            org = db.get(Organization, self.org_id)
            if org is not None:
                org.owner_user_id = None
                db.commit()
            db.query(User).filter(User.org_id == self.org_id).delete()
            db.commit()
            if org is not None:
                db.delete(org)
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


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ── the session endpoint the browser depends on ─────────────────────────────────────────

def test_me_refuses_every_bad_credential(w):
    client = TestClient(m.app)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers=_auth("")).status_code == 401
    assert client.get("/api/auth/me", headers=_auth("not-a-jwt")).status_code == 401
    assert client.get("/api/auth/me",
                      headers=_auth("a.b.c")).status_code == 401
    # A well-formed JWT signed with somebody else's key.
    forged = jwt.encode({"sub": str(w.admin_id), "role": "super_admin",
                         "exp": _now() + timedelta(hours=1)},
                        "not-the-real-secret", algorithm=ALGORITHM)
    assert client.get("/api/auth/me", headers=_auth(forged)).status_code == 401


def test_me_returns_the_caller_for_a_valid_session(w):
    token = w.login(w.admin_email)
    client = TestClient(m.app)
    r = client.get("/api/auth/me", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    # The browser drives its routing off `role`, so it has to be the SERVER's answer.
    assert body["email"] == w.admin_email.lower()
    assert body["role"] == "org_admin"
    assert str(body["id"]) == str(w.admin_id)
    # And it must not hand back anything that could be replayed as a credential.
    for leaked in ("password", "password_hash", "token", "access_token", "secret"):
        assert leaked not in body, leaked


def test_expired_token_is_refused(w):
    """An expired session is exactly as good as no session."""
    expired = jwt.encode({"sub": str(w.admin_id), "role": "org_admin",
                          "exp": _now() - timedelta(minutes=1)},
                         settings.SECRET_KEY, algorithm=ALGORITHM)
    client = TestClient(m.app)
    assert client.get("/api/auth/me", headers=_auth(expired)).status_code == 401
    assert client.get("/api/organization/overview",
                      headers=_auth(expired)).status_code == 401


def test_deactivated_user_is_refused_with_a_live_token(w):
    """The token is still cryptographically valid; the account is not.

    This is why validating against the server matters at all - nothing in the token itself
    changes when an account is disabled.
    """
    token = w.login(w.disabled_email)
    client = TestClient(m.app)
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 200

    db = SessionLocal()
    try:
        user = db.get(User, w.disabled_id)
        user.is_active = False
        db.commit()
    finally:
        db.close()

    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401
    assert client.get("/api/organization/overview",
                      headers=_auth(token)).status_code == 401


def test_token_for_a_deleted_user_is_refused(w):
    """A signed token naming a user who no longer exists resolves to nobody."""
    ghost = jwt.encode({"sub": str(uuid.uuid4()), "role": "super_admin",
                        "exp": _now() + timedelta(hours=1)},
                       settings.SECRET_KEY, algorithm=ALGORITHM)
    client = TestClient(m.app)
    assert client.get("/api/auth/me", headers=_auth(ghost)).status_code == 401


# ── the protected surfaces the browser guards mirror ────────────────────────────────────

def test_protected_endpoints_refuse_an_anonymous_caller(w):
    """Frontend routing is UX. These are the actual gates."""
    client = TestClient(m.app)
    for path in ("/api/auth/me", "/api/organization/overview",
                 "/api/organization/analytics", "/api/organization/members",
                 "/api/admin/dashboard", "/api/admin/organizations",
                 "/api/admin/audit-logs"):
        code = client.get(path).status_code
        assert code in (401, 403, 404), f"{path} answered {code} to an anonymous caller"
        # 404 would mean the path moved; anything 2xx is an open door.
        assert code != 200, f"{path} is reachable without a session"


def test_role_is_enforced_server_side(w):
    """An org_admin token cannot reach the platform console, whatever the browser thinks."""
    token = w.login(w.admin_email)
    client = TestClient(m.app)
    # Their own console works.
    assert client.get("/api/organization/overview",
                      headers=_auth(token)).status_code == 200
    # The super-admin console does not.
    for path in ("/api/admin/dashboard", "/api/admin/organizations"):
        code = client.get(path, headers=_auth(token)).status_code
        assert code == 403, f"{path} answered {code} to an org_admin"

    # And a token whose ROLE CLAIM was tampered with buys nothing: authorization reads the
    # user record, not the claim.
    escalated = jwt.encode({"sub": str(w.admin_id), "role": "super_admin",
                            "exp": _now() + timedelta(hours=1)},
                           settings.SECRET_KEY, algorithm=ALGORITHM)
    assert client.get("/api/admin/dashboard",
                      headers=_auth(escalated)).status_code == 403, (
        "a forged role claim escalated privileges")


def test_public_endpoints_stay_public(w):
    """The fix must not have made the unauthenticated surfaces require a session."""
    client = TestClient(m.app)
    for path in ("/health", "/api/status", "/api/trust", "/api/organization/plans"):
        assert client.get(path).status_code == 200, path


def test_login_failure_is_not_a_session(w):
    """A wrong password answers 401 and issues nothing."""
    client = TestClient(m.app)
    ratelimit._HITS.clear()
    r = client.post("/api/auth/login",
                    json={"identifier": w.admin_email, "password": "wrong-password"},
                    headers={"User-Agent": UA})
    assert r.status_code == 401, r.text
    assert "access_token" not in r.text


def test_me_separates_platform_organization_and_event_roles(w):
    """The role semantics the routing decision depends on.

    `role` is the ACCOUNT persona. `platform_role`/`organization_role` split it into the two
    questions a client asks. NO event role appears - an assignment to one broadcast is not a
    property of the account, and a client that could read one here would be tempted to route
    off it.
    """
    client = TestClient(m.app)

    body = client.get("/api/auth/me", headers=_auth(w.login(w.admin_email))).json()
    assert body["role"] == "org_admin"
    assert body["platform_role"] is None, "an org admin has no platform authority"
    # This account IS the organization's owner (World sets owner_user_id), and "owner" is
    # not a ROLES value - only this field can express it.
    assert body["organization_role"] == "owner"

    staff = client.get("/api/auth/me", headers=_auth(w.login(w.staff_email))).json()
    assert staff["role"] == "super_admin"
    assert staff["platform_role"] == "super_admin"

    # No event role, under any name, for any caller.
    for payload in (body, staff):
        for banned in ("event_role", "eventRole", "assignments", "assigned_events",
                       "can_host", "host", "event_id", "contributor_role"):
            assert banned not in payload, f"/auth/me exposed {banned}"


def test_a_host_persona_is_reported_as_an_ordinary_member(w):
    """A "host" ACCOUNT is a member of the organization, not a host of anything.

    Reporting the persona as the organization role would re-create the confusion the split
    exists to remove - and a client routing off `organization_role` would send them to a
    console again.
    """
    db = SessionLocal()
    try:
        email = _new_email("hostpersona")
        user = User(org_id=w.org_id, full_name="Host Persona", role="host", is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=_now())
        db.add(user)
        db.commit()
    finally:
        db.close()

    body = TestClient(m.app).get("/api/auth/me", headers=_auth(w.login(email))).json()
    assert body["role"] == "host"                    # the persona, unchanged
    assert body["platform_role"] is None
    assert body["organization_role"] == "member"     # NOT "host"


def test_org_dashboard_is_readable_by_any_member(w):
    """The backend already allowed this; the frontend gate now matches it.

    /organization/overview authorizes with get_my_org (any member), so a host-persona
    account asking for the organization dashboard is a legitimate request - and locking it
    out in the router is what bounced them into a Producer Console.
    """
    db = SessionLocal()
    try:
        email = _new_email("member")
        user = User(org_id=w.org_id, full_name="Ordinary Member", role="host",
                    is_active=True, email=email.lower(),
                    username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=_now())
        db.add(user)
        db.commit()
    finally:
        db.close()

    headers = _auth(w.login(email))
    client = TestClient(m.app)
    assert client.get("/api/organization/overview", headers=headers).status_code == 200
    assert client.get("/api/organization/console-state", headers=headers).status_code == 200
    # Genuinely admin-only surfaces stay closed to them: opening the dashboard route did
    # not open the admin API behind the other pages.
    assert client.get("/api/organization/users", headers=headers).status_code == 403


TESTS = [
    test_me_refuses_every_bad_credential,
    test_me_returns_the_caller_for_a_valid_session,
    test_expired_token_is_refused,
    test_deactivated_user_is_refused_with_a_live_token,
    test_token_for_a_deleted_user_is_refused,
    test_protected_endpoints_refuse_an_anonymous_caller,
    test_role_is_enforced_server_side,
    test_public_endpoints_stay_public,
    test_login_failure_is_not_a_session,
    test_me_separates_platform_organization_and_event_roles,
    test_a_host_persona_is_reported_as_an_ordinary_member,
    test_org_dashboard_is_readable_by_any_member,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
