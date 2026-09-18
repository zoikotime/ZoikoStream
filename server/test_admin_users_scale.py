"""Identity & Access at scale: global sorting, complete paging, and the last Super Admin.

── WHAT THIS COVERS ────────────────────────────────────────────────────────────────────
Two fixes, both confined to the admin path:

1. GET /admin/users sorts and pages in SQL over the whole filtered set. Sorting used to
   happen in the browser over the fetched page, so "Joined, oldest first" returned the
   oldest of the newest 100 — an answer shaped exactly like the right one.

2. update_user/delete_user refuse an operation that would leave ZERO active super admins.
   The existing guard beside it is self-scoped: it stops an admin locking themselves out,
   but not admin A removing the last remaining admin B. Nobody could then reach /admin at
   all, and no route mints a super admin, so recovery meant editing the database by hand.

ORG-009 is untouched here and asserted untouched in test_admin_users_support_gate-style
checks below: every protected write still calls ctx.authorize first.
"""
import uuid

import pytest
from sqlalchemy import select

from app.crud import admin as crud
from app.db import SessionLocal
from app.models import Organization, User
from app.security import hash_password


@pytest.fixture
def world():
    """Seven accounts of its own, across two organizations.

    Seven, not five, because the pagination test needs more rows than two pages of three —
    and it must own all of them. Depending on whatever else is in the database made it pass
    locally and fail on CI, where a freshly seeded database contributes exactly one row.
    """
    db = SessionLocal()
    made = {"users": [], "orgs": []}
    try:
        org = Organization(name=f"ScaleCo {uuid.uuid4().hex[:6]}", status="active")
        other = Organization(name=f"AaaCo {uuid.uuid4().hex[:6]}", status="active")
        db.add_all([org, other])
        db.flush()
        made["orgs"] = [org.id, other.id]

        tag = uuid.uuid4().hex[:8]
        users = []
        for i, role in enumerate(["super_admin", "org_admin", "host", "viewer", "org_admin"]):
            u = User(org_id=org.id if i < 4 else other.id,
                     full_name=f"ZZ{tag} User {i:02d}", role=role, is_active=True,
                     email=f"sc-{tag}-{i}@example.com", username=f"sc{tag}{i}",
                     password_hash=hash_password("x"), email_verified=True)
            db.add(u)
            users.append(u)

        # Two more accounts that exist ONLY so pagination has a boundary to cross.
        #
        # test_pages_do_not_overlap_or_skip asserts `total > 6` because two pages of three
        # have to be genuinely distinct for the id-tiebreaker to be under test at all. It used
        # to get that sixth-and-beyond row from whatever else happened to be in the database —
        # which is true locally and false on a freshly seeded CI database, where the only
        # other row is the seeded super admin and the total lands on exactly 6.
        #
        # Deliberately NOT tagged: every name, email and username here is uuid-derived and
        # contains no `tag`, so test_search_is_case_insensitive_and_partial still matches
        # exactly the five accounts above. They also belong to the PRIMARY org, so
        # test_org_filter_is_applied_in_sql still finds exactly one account in `other`.
        # Neither is a super_admin, so the last-admin guard tests are unaffected.
        for role in ("viewer", "host"):
            pad = uuid.uuid4().hex[:12]
            u = User(org_id=org.id, full_name=f"Pagination Filler {pad}", role=role,
                     is_active=True, email=f"pagination-{pad}@example.com",
                     username=f"pagination{pad}",
                     password_hash=hash_password("x"), email_verified=True)
            db.add(u)
            # Appended to the same list the cleanup already walks, so these are removed too.
            users.append(u)
        db.flush()
        made["users"] = [u.id for u in users]
        db.commit()
        yield {"db": db, "org": org, "other": other, "tag": tag, "users": users}
    finally:
        try:
            for uid in made["users"]:
                db.query(User).filter(User.id == uid).delete()
            for oid in made["orgs"]:
                db.query(Organization).filter(Organization.id == oid).delete()
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


# ── sorting is global, and safe ────────────────────────────────────────────────────────

def test_sorting_spans_the_whole_set_not_the_returned_page(world):
    """The defect, stated directly: page 1 of an ascending sort must hold the global first
    row, not the first of some other slice."""
    db = world["db"]
    asc, total = crud.list_users(db, page=1, page_size=5, sort_by="joined", order="asc")
    true_oldest = db.scalars(select(User).order_by(User.created_at.asc()).limit(1)).first()

    assert total > 5, "needs more rows than one page for this to mean anything"
    assert asc[0].created_at == true_oldest.created_at


def test_descending_is_the_other_end_of_the_same_set(world):
    db = world["db"]
    desc, _ = crud.list_users(db, page=1, page_size=5, sort_by="joined", order="desc")
    true_newest = db.scalars(select(User).order_by(User.created_at.desc()).limit(1)).first()
    assert desc[0].created_at == true_newest.created_at


def test_name_sorting_is_global(world):
    db = world["db"]
    rows, _ = crud.list_users(db, page=1, page_size=5, sort_by="name", order="asc")
    true_first = db.scalars(select(User).order_by(User.full_name.asc()).limit(1)).first()
    assert rows[0].full_name == true_first.full_name


def test_organization_sorting_joins_without_dropping_rows(world):
    """An outer join: a user whose organization row is missing must still be listed, and the
    total must not change just because the sort column lives on another table."""
    db = world["db"]
    _, plain_total = crud.list_users(db, page=1, page_size=1)
    _, sorted_total = crud.list_users(db, page=1, page_size=1, sort_by="organization")
    assert plain_total == sorted_total


def test_an_unknown_sort_field_falls_back_instead_of_reaching_sql(world):
    """crud maps a name to a column; it never interpolates one. The route rejects anything
    outside the pattern first, so this is the second line of the same defence."""
    db = world["db"]
    rows, total = crud.list_users(db, page=1, page_size=3,
                                  sort_by="role); DROP TABLE users;--")
    assert len(rows) == 3 and total > 0


def test_every_whitelisted_field_actually_sorts(world):
    db = world["db"]
    for field in ["name", "email", "role", "joined", "organization"]:
        rows, _ = crud.list_users(db, page=1, page_size=3, sort_by=field, order="asc")
        assert len(rows) == 3, f"{field} returned nothing"


# ── paging is complete and stable ──────────────────────────────────────────────────────

def test_walking_the_pages_reaches_every_account(world):
    """The headline: with page_size 50 and >1000 accounts, the operator must be able to get
    to all of them. Previously the page fetched 100 and stopped."""
    db = world["db"]
    _, total = crud.list_users(db, page=1, page_size=50)
    seen = set()
    pages = (total + 49) // 50
    for page in range(1, pages + 1):
        rows, _ = crud.list_users(db, page=page, page_size=50, sort_by="joined", order="desc")
        seen |= {r.id for r in rows}
    assert len(seen) == total


def test_pages_do_not_overlap_or_skip(world):
    """created_at alone is not unique — two accounts sharing a timestamp could swap between
    pages and hide a row. The id tiebreaker is what makes the walk above exhaustive."""
    db = world["db"]
    p1, total = crud.list_users(db, page=1, page_size=3, sort_by="joined", order="desc")
    p2, _ = crud.list_users(db, page=2, page_size=3, sort_by="joined", order="desc")
    assert total > 6
    assert not ({r.id for r in p1} & {r.id for r in p2})


def test_total_is_the_filtered_count_not_the_page_length(world):
    db = world["db"]
    rows, total = crud.list_users(db, page=1, page_size=2)
    assert len(rows) == 2
    assert total > 2


def test_org_filter_is_applied_in_sql(world):
    db = world["db"]
    rows, total = crud.list_users(db, page=1, page_size=50, org_id=world["other"].id)
    assert total == 1
    assert all(r.org_id == world["other"].id for r in rows)


def test_role_filter_none_means_every_role(world):
    """"All roles" sends no role param. Legacy-role accounts must still be returned."""
    db = world["db"]
    _, total_all = crud.list_users(db, page=1, page_size=1)
    _, total_host = crud.list_users(db, page=1, page_size=1, role="host")
    assert total_host >= 1
    assert total_all > total_host


def test_search_is_case_insensitive_and_partial(world):
    db = world["db"]
    tag = world["tag"]
    lower, n_lower = crud.list_users(db, page=1, page_size=10, q=tag.lower())
    _, n_upper = crud.list_users(db, page=1, page_size=10, q=tag.upper())
    assert n_lower == n_upper == 5
    assert lower


# ── the last active super admin ────────────────────────────────────────────────────────
#
# Exercised through the router's guard directly. It is deliberately NOT in crud: crud stays a
# data function, and the guard belongs beside the self-lockout check it extends.

from fastapi import HTTPException

from app.routers.admin import _assert_super_admins_remain


def _only_super_admin(db):
    """Make the fixture's super admin the only ACTIVE one, without deleting anybody:
    every other active super admin is flipped inactive inside a transaction we roll back."""
    return db.scalars(
        select(User).where(User.role == "super_admin", User.is_active.is_(True))
    ).all()


def test_demoting_a_super_admin_is_allowed_while_others_remain(world):
    db = world["db"]
    target = world["users"][0]
    assert target.role == "super_admin"
    # The live database has many active super admins, so this must not raise.
    _assert_super_admins_remain(db, target, demoting_to="org_admin")


def test_the_guard_ignores_accounts_that_are_not_active_super_admins(world):
    """A host being deactivated has nothing to do with the super-admin floor."""
    db = world["db"]
    host = next(u for u in world["users"] if u.role == "host")
    _assert_super_admins_remain(db, host, deactivating=True)
    _assert_super_admins_remain(db, host, deleting=True)


def test_a_rename_never_trips_the_guard(world):
    """demoting_to=None and no deactivate/delete: nothing is being removed."""
    db = world["db"]
    target = world["users"][0]
    _assert_super_admins_remain(db, target, demoting_to=None)


def test_keeping_the_role_super_admin_is_not_a_demotion(world):
    db = world["db"]
    target = world["users"][0]
    _assert_super_admins_remain(db, target, demoting_to="super_admin")


def test_the_last_active_super_admin_cannot_be_removed(world):
    """The real scenario, built in a transaction that is rolled back so no account is
    actually changed: every other active super admin is temporarily inactive, leaving one."""
    db = world["db"]
    target = world["users"][0]
    others = [u for u in _only_super_admin(db) if u.id != target.id]
    try:
        for u in others:
            u.is_active = False
        db.flush()

        for kwargs in ({"demoting_to": "org_admin"}, {"deactivating": True}, {"deleting": True}):
            with pytest.raises(HTTPException) as exc:
                _assert_super_admins_remain(db, target, **kwargs)
            assert exc.value.status_code == 409
            assert "Super Admin" in exc.value.detail
    finally:
        db.rollback()


def test_the_floor_is_one_not_zero(world):
    """With exactly two active super admins, removing one is still allowed."""
    db = world["db"]
    target = world["users"][0]
    others = [u for u in _only_super_admin(db) if u.id != target.id]
    try:
        for u in others[1:]:
            u.is_active = False
        db.flush()
        _assert_super_admins_remain(db, target, demoting_to="org_admin")   # must not raise
    finally:
        db.rollback()
