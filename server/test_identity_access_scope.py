"""Identity & Access lists the platform's administrators, not its entire user table.

── WHAT CHANGED ────────────────────────────────────────────────────────────────────────
The console could only ever ASSIGN two roles (super_admin, org_admin) and the Role filter
only ever offered those two — but "All roles" sent no `role` parameter, so the query applied
no predicate and returned every account on the platform. On this deployment that was 1,128
rows, overwhelmingly Hosts, Speakers and Viewers that this page cannot grant, cannot revoke
and is not where anyone administers. The KPI row counted them too, so the cards described a
different population than the table underneath them.

The scope now lives in crud.admin.IDENTITY_ACCESS_ROLES and is applied in ONE place, which
is what these pin: not "the default is narrower now" but that there is no parameter
combination — role, search, sort, page — that reaches past it. Nothing is deleted; Host and
Viewer accounts are untouched and still administered where they are granted.
"""
import uuid

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.crud.admin import IDENTITY_ACCESS_ROLES
from app.db import SessionLocal
from app.models import Organization, User
from app.security import create_access_token, hash_password

HIDDEN_ROLES = ("host", "speaker", "viewer", "billing_admin")


@pytest.fixture
def world():
    db = SessionLocal()
    made = {"u": [], "o": []}
    try:
        org = Organization(name=f"IdCo {uuid.uuid4().hex[:6]}", status="active")
        other = Organization(name=f"OtherCo {uuid.uuid4().hex[:6]}", status="active")
        db.add_all([org, other])
        db.flush()
        made["o"] = [org.id, other.id]

        tag = uuid.uuid4().hex[:8]

        def mk(role, org_id, name):
            u = User(org_id=org_id, full_name=name, role=role, is_active=True,
                     email=f"{role}-{tag}-{uuid.uuid4().hex[:8]}@example.com",
                     username=f"{role[:4]}{uuid.uuid4().hex[:10]}",
                     password_hash=hash_password("x"), email_verified=True)
            db.add(u)
            db.flush()
            made["u"].append(u.id)
            return u

        people = {
            "admin": mk("super_admin", org.id, f"Zed Superadmin {tag}"),
            "orgadmin": mk("org_admin", org.id, f"Zed Orgadmin {tag}"),
            "orgadmin_other": mk("org_admin", other.id, f"Zed Elsewhere {tag}"),
        }
        for role in HIDDEN_ROLES:
            people[role] = mk(role, org.id, f"Zed {role.title()} {tag}")
        db.commit()
        yield {"db": db, "org": org, "other": other, "tag": tag, "made": made, **people}
    finally:
        try:
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


def listing(world, **params):
    """The page's own request. Scoped to this fixture's accounts via the unique tag, so a
    shared test database's other rows cannot make an assertion pass or fail by accident."""
    c = client_for(world["admin"])
    params.setdefault("q", world["tag"])
    params.setdefault("page_size", 100)
    r = c.get("/api/admin/users", params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def roles_in(items):
    return {u["role"] for u in items}


# ── the two that belong here ───────────────────────────────────────────────────────────

def test_super_admin_appears(world):
    assert any(u["id"] == str(world["admin"].id) for u in listing(world))


def test_org_admin_appears(world):
    assert any(u["id"] == str(world["orgadmin"].id) for u in listing(world))


# ── the ones that do not ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", HIDDEN_ROLES)
def test_a_non_platform_role_is_not_listed(world, role):
    """Host, Speaker, Viewer, Billing Admin. Each is granted inside an organization or per
    event; none is a platform appointment, and none is administered from this console."""
    items = listing(world)
    assert role not in roles_in(items)
    assert not any(u["id"] == str(world[role].id) for u in items)


def test_all_roles_means_the_two_platform_roles(world):
    """The actual defect. No `role` parameter used to mean no predicate at all."""
    assert roles_in(listing(world)) <= set(IDENTITY_ACCESS_ROLES)


def test_the_hidden_accounts_still_exist(world):
    """A visibility change, never a deletion. Every one of them is still in the database and
    still required by events, registrations and historical data."""
    world["db"].expire_all()
    for role in HIDDEN_ROLES:
        row = world["db"].get(User, world[role].id)
        assert row is not None and row.role == role and row.is_active


# ── the scope cannot be widened from the query string ──────────────────────────────────

@pytest.mark.parametrize("role", HIDDEN_ROLES)
def test_asking_for_a_hidden_role_directly_is_refused(world, role):
    """A direct API call must not reach past the page's scope. 422 rather than an empty page:
    the role is real elsewhere in the product, so failing loudly is clearer than pretending
    there are none."""
    c = client_for(world["admin"])
    r = c.get("/api/admin/users", params={"role": role})
    assert r.status_code == 422, r.text


def test_the_super_admin_filter_narrows_within_the_scope(world):
    items = listing(world, role="super_admin")
    assert roles_in(items) == {"super_admin"}


def test_the_org_admin_filter_narrows_within_the_scope(world):
    items = listing(world, role="org_admin")
    assert roles_in(items) == {"org_admin"}


def test_search_never_returns_a_hidden_role(world):
    """Every fixture account — including the Hosts and Viewers — shares this tag in its name
    and email, so a search that reached past the scope would return all seven, not three."""
    items = listing(world, q=world["tag"])
    assert len(items) == 3, f"expected the 3 platform accounts, got {len(items)}"
    assert roles_in(items) <= set(IDENTITY_ACCESS_ROLES)


@pytest.mark.parametrize("sort_by", ["name", "email", "role", "joined", "organization"])
def test_no_sort_order_surfaces_a_hidden_role(world, sort_by):
    assert roles_in(listing(world, sort_by=sort_by, order="asc")) <= set(IDENTITY_ACCESS_ROLES)


def test_pagination_never_leaks_a_hidden_role(world):
    """Walked page by page at size 1, so a leak on any page fails rather than only page 1."""
    seen = []
    for page in (1, 2, 3, 4):
        seen.extend(listing(world, page=page, page_size=1))
    assert roles_in(seen) <= set(IDENTITY_ACCESS_ROLES)
    assert len({u["id"] for u in seen}) == 3


def test_the_organization_filter_still_works(world):
    items = listing(world, org_id=str(world["other"].id))
    assert [u["id"] for u in items] == [str(world["orgadmin_other"].id)]


# ── the KPI row describes the rows beneath it ──────────────────────────────────────────

def test_the_summary_counts_only_the_two_platform_roles(world):
    """The cards read "Total 1,128" over a list that could never contain more than a few
    dozen. A KPI describing a different population than the table is worse than no KPI."""
    c = client_for(world["admin"])
    summary = c.get("/api/admin/users/summary").json()

    db = world["db"]
    db.expire_all()
    from sqlalchemy import func, select

    expected_total = db.scalar(
        select(func.count()).select_from(User).where(User.role.in_(IDENTITY_ACCESS_ROLES))
    )
    expected_supers = db.scalar(
        select(func.count()).select_from(User).where(User.role == "super_admin")
    )
    every_user = db.scalar(select(func.count()).select_from(User))

    assert summary["total"] == expected_total
    assert summary["super_admins"] == expected_supers
    assert summary["active"] + summary["inactive"] == expected_total
    if every_user > expected_total:
        assert summary["total"] < every_user, "the summary must not count the whole user table"


# ── the safeguards it sits in front of are untouched ───────────────────────────────────

def test_the_final_super_admin_is_still_protected(world):
    """Narrowing what is LISTED must not change what is allowed. Demoting the last active
    super admin is still refused, and this fixture's own super admin is not the last one on a
    shared database — so the guard is exercised directly rather than through the endpoint."""
    from fastapi import HTTPException

    from app.routers.admin import _assert_super_admins_remain

    db = world["db"]
    others = db.query(User).filter(
        User.role == "super_admin", User.is_active.is_(True), User.id != world["admin"].id
    ).count()

    if others:
        # Not the last one: the guard must allow it.
        _assert_super_admins_remain(db, world["admin"], demoting_to="org_admin")
    else:
        with pytest.raises(HTTPException) as exc:
            _assert_super_admins_remain(db, world["admin"], demoting_to="org_admin")
        assert exc.value.status_code == 409

    db.rollback()
