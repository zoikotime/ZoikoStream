"""Self-check for Phase 3: invitation crud/token logic + endpoint authorization.
Pure crud tests fake the Session; the gating test uses TestClient and never touches the
DB (401/422 are decided before any query). Run: `python test_invitations.py`."""
from types import SimpleNamespace

from starlette.testclient import TestClient

import app.main as m
from app.crud import organization as crud
from app.routers.organization import router as orgr


class FakeDB:
    def add(self, _): pass
    def commit(self): pass
    def refresh(self, _): pass
    def scalar(self, _stmt): return None


# ── crud / token security ─────────────────────────────────────────────────────

def test_token_is_hashed_not_stored():
    inv, raw = crud.create_invitation(FakeDB(), org_id="o1", email="A@X.com",
                                      role="host", invited_by_id="u1")
    assert len(raw) >= 32                       # secrets.token_urlsafe(32)
    assert inv.token_hash == crud._hash_token(raw)
    assert inv.token_hash != raw                # raw never stored
    assert len(inv.token_hash) == 64            # sha256 hex
    assert inv.status == "pending" and inv.email == "a@x.com"  # email normalized
    assert inv.expires_at is not None


def test_hash_is_deterministic():
    assert crud._hash_token("abc") == crud._hash_token("abc")
    assert crud._hash_token("abc") != crud._hash_token("abd")


def test_resend_rotates_token_and_resets():
    inv, raw1 = crud.create_invitation(FakeDB(), "o1", "a@x.com", "viewer", "u1")
    inv.status = "expired"
    inv2, raw2 = crud.resend_invitation(FakeDB(), inv)
    assert raw2 != raw1 and inv2.token_hash == crud._hash_token(raw2)
    assert inv2.status == "pending" and inv2.accepted_at is None


def test_unique_username_appends_suffix():
    taken = {"bob", "bob1"}
    orig = crud.username_taken
    crud.username_taken = lambda db, name: name.lower() in taken
    try:
        assert crud.unique_username(FakeDB(), "bob") == "bob2"
        assert crud.unique_username(FakeDB(), "alice") == "alice"
    finally:
        crud.username_taken = orig


def test_accept_creates_member_and_marks_accepted():
    inv = SimpleNamespace(org_id="o1", email="a@x.com", role="host",
                          status="pending", accepted_at=None)
    user = crud.accept_invitation(FakeDB(), inv, "Ann", "ann", "hash")
    assert user.org_id == "o1" and user.role == "host" and user.is_active is True
    assert inv.status == "accepted" and inv.accepted_at is not None


def test_soft_delete_sets_marker_and_deactivates():
    u = SimpleNamespace(is_active=True, deleted_at=None)
    crud.soft_delete_user(FakeDB(), u)
    assert u.is_active is False and u.deleted_at is not None


# ── endpoint authorization (no DB touched) ──────────────────────────────────────

def test_authorization_gating():
    client = TestClient(m.app)
    public = {"/organization/invitations/accept", "/organization/invitations/reject"}
    for r in orgr.routes:
        for method in sorted(x for x in r.methods if x != "HEAD"):
            resp = client.request(method, r.path.replace("{user_id}", "11111111-1111-1111-1111-111111111111")
                                              .replace("{invitation_id}", "11111111-1111-1111-1111-111111111111"),
                                  json={})
            if r.path in public:
                # public + reachable without a token → body validation (missing token), not 401
                assert resp.status_code == 422, f"{r.path} should be public, got {resp.status_code}"
            else:
                assert resp.status_code == 401, f"{method} {r.path} unprotected: {resp.status_code}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("invitations self-check passed")
