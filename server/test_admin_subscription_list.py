"""Read-only behavior tests for admin subscription row identity and current selection."""
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from app.crud import admin as admin_crud


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _ReadOnlyDB:
    def __init__(self, rows):
        self.rows = rows

    def scalar(self, _statement):
        return len(self.rows)

    def scalars(self, _statement):
        return _Rows(self.rows)


def _subscription(sub_id, org_id, *, status, started_at):
    return SimpleNamespace(
        id=uuid.UUID(sub_id),
        org_id=uuid.UUID(org_id),
        organization=SimpleNamespace(name="zoiko"),
        plan=SimpleNamespace(name="Developer", price_monthly=49),
        status=status,
        seats=1,
        started_at=started_at,
        current_period_end=None,
        trial_ends_at=None,
    )


def test_list_keeps_history_and_marks_the_same_current_record_as_org_selector():
    org_id = "10000000-0000-0000-0000-000000000001"
    other_org_id = "10000000-0000-0000-0000-000000000002"
    newest = datetime(2026, 9, 29, tzinfo=timezone.utc)
    older = datetime(2026, 9, 23, tzinfo=timezone.utc)
    rows = [
        _subscription("20000000-0000-0000-0000-000000000001", org_id,
                      status="active", started_at=newest),
        _subscription("20000000-0000-0000-0000-000000000002", org_id,
                      status="trialing", started_at=older),
        _subscription("20000000-0000-0000-0000-000000000003", other_org_id,
                      status="trialing", started_at=older),
    ]

    subscriptions, total = admin_crud.list_subscriptions(_ReadOnlyDB(rows))

    assert total == 3
    assert [item.id for item in subscriptions] == [row.id for row in rows]
    assert [item.org_id for item in subscriptions[:2]] == [rows[0].org_id, rows[1].org_id]
    assert [item.is_current for item in subscriptions] == [True, False, True]
    assert [item.status for item in subscriptions] == ["active", "trialing", "trialing"]
