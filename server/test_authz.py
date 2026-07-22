"""Pure self-check for the Phase 1 authorization ladder and org isolation.
No DB, no server — run with `python test_authz.py` (or pytest)."""
from types import SimpleNamespace

from fastapi import HTTPException

from app.security import require_min_role, require_org_admin, org_scoped


def _user(role):
    return SimpleNamespace(role=role, org_id="org-1")


def test_role_ladder():
    host_gate = require_min_role("host")
    # host and everything above clears the host gate
    for role in ("host", "org_admin", "super_admin"):
        assert host_gate(_user(role)).role == role
    # everything below is rejected
    for role in ("moderator", "speaker", "viewer", "nonsense"):
        try:
            host_gate(_user(role))
            assert False, f"{role} should be denied"
        except HTTPException as e:
            assert e.status_code == 403

    # org_admin gate: org_admin passes, host does not, super_admin bypasses
    assert require_org_admin(_user("org_admin")).role == "org_admin"
    assert require_org_admin(_user("super_admin")).role == "super_admin"
    try:
        require_org_admin(_user("host"))
        assert False, "host should not clear org_admin gate"
    except HTTPException as e:
        assert e.status_code == 403


def test_org_scoping():
    # A fake statement records whether .where() was called.
    calls = []
    model = SimpleNamespace(org_id="COL")
    stmt = SimpleNamespace(where=lambda cond: calls.append(cond) or "scoped")

    # super_admin: untouched, no filter applied
    assert org_scoped(stmt, model, _user("super_admin")) is stmt
    assert calls == []

    # everyone else: filtered by org_id
    assert org_scoped(stmt, model, _user("org_admin")) == "scoped"
    assert len(calls) == 1


if __name__ == "__main__":
    test_role_ladder()
    test_org_scoping()
    print("authz self-check passed")
