"""Why DELETE /admin/organizations/{id} refuses, and how it says so.

The endpoint guarded exactly one thing: orphaned users. Everything else an organization owns
was left to the database, and the database refuses — 24 tables carry a foreign key to
`organizations` and only two of them (`audience_sessions`, `join_denials`) declare
ON DELETE CASCADE. crud.delete_organization clears `subscriptions` and the legacy
`memberships` table itself, which leaves TWENTY tables that can each stop the delete:
events, support tickets, invitations, webhook endpoints, the commercial account, entitlement
state, usage reports, governance records and the rest.

So an organization with no users but any history at all reached `db.delete(org)` and raised
ForeignKeyViolation -> IntegrityError -> an unhandled 500, and the console's toast carried
whatever that turned into. Nothing identified the records holding it.

crud.organization_delete_blockers now reports them, discovered from the mapper metadata
rather than from a list someone has to remember to update — a hand-kept list goes stale the
first time a table is added, and the symptom of staleness is exactly the 500 it replaced.

These tests touch no database: the counting is driven by metadata, so a stub session that
answers db.scalar() is enough to drive every branch.
"""
import pytest

from app.crud.admin import organization_delete_blockers
from app.db import Base

ORG_ID = "11111111-1111-1111-1111-111111111111"


def _org_fk_tables():
    """Every table with a non-cascading FK to organizations — the ones that can block."""
    org = Base.metadata.tables["organizations"]
    out = []
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table is org and (fk.ondelete or "").upper() != "CASCADE":
                out.append(table.name)
                break
    return set(out)


class StubSession:
    """Answers every count with `counts[table_name]`, defaulting to 0."""

    def __init__(self, counts=None):
        self.counts = counts or {}
        self.queried = []

    def scalar(self, stmt):
        # The statement's FROM is the dependent table; that is all this needs to know.
        table = list(stmt.get_final_froms())[0].name
        self.queried.append(table)
        return self.counts.get(table, 0)


def test_the_schema_still_has_many_non_cascading_dependents():
    # If this ever drops to nothing, the guard is pointless and should be revisited; if it
    # grows, the guard picks the new table up automatically — that is the point of it.
    assert len(_org_fk_tables()) > 10


def test_an_organization_that_owns_nothing_has_no_blockers():
    assert organization_delete_blockers(StubSession(), ORG_ID) == []


def test_it_names_what_is_holding_the_organization():
    blockers = organization_delete_blockers(StubSession({"events": 2, "invitations": 1}), ORG_ID)
    assert ("event(s)", 2) in blockers
    assert ("invitation(s)", 1) in blockers


def test_the_biggest_blocker_is_reported_first():
    blockers = organization_delete_blockers(
        StubSession({"events": 2, "support_tickets": 9, "invitations": 1}), ORG_ID)
    assert [n for _label, n in blockers] == [9, 2, 1]


def test_users_are_left_to_the_endpoint_s_own_message():
    # The router reports users separately and more precisely ("remove them before deleting"),
    # so counting them here would say the same thing twice.
    stub = StubSession({"users": 5})
    assert organization_delete_blockers(stub, ORG_ID) == []
    assert "users" not in stub.queried


@pytest.mark.parametrize("cleared", ["subscriptions", "memberships"])
def test_tables_the_delete_clears_itself_are_not_blockers(cleared):
    stub = StubSession({cleared: 3})
    assert organization_delete_blockers(stub, ORG_ID) == []
    assert cleared not in stub.queried


@pytest.mark.parametrize("cascading", ["audience_sessions", "join_denials"])
def test_cascading_tables_are_never_counted(cascading):
    # A cascading FK cleans itself up, so it can never be the reason a delete fails.
    stub = StubSession({cascading: 100})
    assert organization_delete_blockers(stub, ORG_ID) == []
    assert cascading not in stub.queried


def test_every_blocking_table_is_actually_checked():
    stub = StubSession()
    organization_delete_blockers(stub, ORG_ID)
    expected = _org_fk_tables() - {"users", "subscriptions", "memberships"}
    assert set(stub.queried) == expected


def test_each_table_is_counted_once_even_with_several_org_columns():
    stub = StubSession()
    organization_delete_blockers(stub, ORG_ID)
    assert len(stub.queried) == len(set(stub.queried))


def test_unlabelled_tables_fall_back_to_their_table_name():
    # A new table with no friendly label still reports something actionable rather than
    # being silently dropped from the message.
    from app.crud import admin as crud_admin

    unlabelled = sorted(_org_fk_tables()
                        - set(crud_admin._ORG_DEPENDENT_LABELS)
                        - {"users", "subscriptions", "memberships"})
    if not unlabelled:
        pytest.skip("every dependent currently has a label")
    name = unlabelled[0]
    assert organization_delete_blockers(StubSession({name: 1}), ORG_ID) == [(name, 1)]
