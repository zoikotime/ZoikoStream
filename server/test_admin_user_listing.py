"""GET /admin/users — the contract at the API boundary, and what one bad row costs.

The Identity & Access console reported HTTP 500 on selecting a role filter. The request
itself is sound at every layer that can be read statically: the console sends canonical
values (`role === "all" ? undefined : role`), the route validates `role` against
^(super_admin|org_admin)$ so an unknown value is a 422, and the query adds a plain
`WHERE role = :role` on top of the scope predicate — no join, no cast, no alias.

What was NOT sound is serialization. `AdminUserOut.org_id` was required while every other
layer treated a user's organization as something that may not resolve:

    crud._user_out:  organization_name = u.organization.name if u.organization else None
    the console:     render: (u) => u.organization_name || "—"
    this same file:  AdminAuditOut.org_id: uuid.UUID | None = None

So a user row whose organization does not resolve raised ValidationError *inside a list
comprehension over the page* — HTTP 500 for the whole console rather than a dash in one cell.
A role filter changes which 20 of 2,175 rows get serialized, which is how a listing that
loads unfiltered can fail the moment a filter is applied.

These tests touch no database: the serializer and the route's declared validation are both
readable without one. The live query is NOT covered here — see the note in the final report.
"""
import uuid

import pytest
from pydantic import ValidationError

from app.crud.admin import IDENTITY_ACCESS_ROLES, USER_SORTS, _user_out
from app.schemas.admin import AdminUserOut


class Row:
    """A users row as the ORM hands it to the serializer."""

    def __init__(self, **over):
        self.id = uuid.uuid4()
        self.full_name = "Ada Ops"
        self.email = "ada@example.com"
        self.username = "ada"
        self.role = "super_admin"
        self.is_active = True
        self.org_id = uuid.uuid4()
        self.created_at = None
        self.staff_commercial_role = None
        self.avatar_url = None
        self.organization = None
        self.__dict__.update(over)


# ── the field that caused it ────────────────────────────────────────────────────────────

def test_a_user_whose_organization_does_not_resolve_still_serialises():
    out = _user_out(Row(org_id=None, organization=None))
    assert out is not None
    assert out.org_id is None
    assert out.organization_name is None


def test_org_id_is_optional_like_every_other_layer_already_assumed():
    assert AdminUserOut.model_fields["org_id"].is_required() is False


def test_the_organization_name_is_still_filled_in_when_it_does_resolve():
    org = type("Org", (), {"name": "Acme"})()
    assert _user_out(Row(organization=org)).organization_name == "Acme"


# ── one bad row must not cost the page ──────────────────────────────────────────────────

def test_an_unserialisable_row_is_dropped_rather_than_raised():
    # email is still required; this stands in for the next field that diverges.
    assert _user_out(Row(email=None)) is None


def test_the_dropped_row_is_identified_in_the_log(caplog):
    row = Row(email=None)
    with caplog.at_level("ERROR"):
        _user_out(row)
    assert str(row.id) in caplog.text, "the log must name the row so it can be found"


def test_the_failure_does_not_reach_the_caller_as_an_exception():
    # The regression: this ran inside a list comprehension, so raising here was a 500 for the
    # whole listing rather than one missing row.
    for bad in (Row(email=None), Row(role=None), Row(is_active=None), Row(full_name=None)):
        assert _user_out(bad) is None


def test_a_good_row_is_unaffected_by_the_guard():
    out = _user_out(Row())
    assert out is not None and out.role == "super_admin" and out.username == "ada"


# ── the boundary: what the route will and will not accept ───────────────────────────────

def _users_get_params():
    from app.main import app

    return {p["name"]: p for p in app.openapi()["paths"]["/api/admin/users"]["get"]["parameters"]}


@pytest.mark.parametrize("role", ["super_admin", "org_admin"])
def test_the_two_supported_roles_are_accepted(role):
    import re
    pattern = _users_get_params()["role"]["schema"]["anyOf"][0]["pattern"]
    assert re.fullmatch(pattern, role)


@pytest.mark.parametrize("role", ["all", "host", "viewer", "billing_admin", "", "SUPER_ADMIN", "' OR 1=1"])
def test_an_unsupported_role_is_refused_at_the_boundary(role):
    # A 422 naming the field, never a 500 and never a silently empty page.
    import re
    pattern = _users_get_params()["role"]["schema"]["anyOf"][0]["pattern"]
    assert not re.fullmatch(pattern, role)


def test_role_is_optional_so_the_unfiltered_listing_is_a_real_request():
    assert _users_get_params()["role"]["required"] is False


@pytest.mark.parametrize("field", ["name", "email", "role", "joined", "organization"])
def test_every_offered_sort_is_one_the_query_can_map(field):
    import re
    pattern = _users_get_params()["sort_by"]["schema"]["anyOf"][0]["pattern"]
    assert re.fullmatch(pattern, field)
    # "organization" maps to None in USER_SORTS and is resolved by an outerjoin in list_users.
    assert field in USER_SORTS


def test_an_unknown_sort_cannot_reach_the_query():
    import re
    pattern = _users_get_params()["sort_by"]["schema"]["anyOf"][0]["pattern"]
    for bad in ["password_hash", "id; DROP TABLE users", "created_at"]:
        assert not re.fullmatch(pattern, bad)


# ── scope ───────────────────────────────────────────────────────────────────────────────

def test_the_listing_is_scoped_to_the_two_platform_appointments():
    # The role filter can only narrow within this; it can never widen the listing.
    assert IDENTITY_ACCESS_ROLES == ("super_admin", "org_admin")


def test_a_role_filter_cannot_reach_past_that_scope():
    # Both supported filter values are inside the scope, so the AND can never widen it.
    import re
    pattern = _users_get_params()["role"]["schema"]["anyOf"][0]["pattern"]
    accepted = [r for r in ("super_admin", "org_admin", "host", "viewer") if re.fullmatch(pattern, r)]
    assert set(accepted) <= set(IDENTITY_ACCESS_ROLES)


# ── schema / model parity: the class of bug, not just this instance ─────────────────────

def test_no_required_response_field_maps_to_a_column_the_database_may_leave_null():
    """The check that would have caught this before it reached production.

    A required field on the response schema is a promise about the column behind it. Where
    the two disagree, the disagreement surfaces as a 500 from inside the serializer, on
    whichever page happens to contain the offending row.
    """
    from app.models.user import User

    columns = User.__table__.columns
    for name, field in AdminUserOut.model_fields.items():
        if not field.is_required():
            continue
        col = columns.get(name)
        if col is None:
            continue            # derived, not a column (organization_name)
        assert not col.nullable, (
            f"AdminUserOut.{name} is required but users.{name} is nullable — a row with a "
            f"NULL there raises inside the listing serializer")
