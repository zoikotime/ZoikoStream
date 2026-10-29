from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.admin import PlanOut, SettingsUpdate
from app.services.quota import resolve_effective_limit
from app.services import org as org_svc


@pytest.mark.parametrize(
    "args, expected",
    [
        (
            dict(has_subscription=False, entitled=False, plan_limit=None,
                 fallback_limit=50),
            {"limit": 50, "configured": True, "source": "platform_default"},
        ),
        (
            dict(has_subscription=False, entitled=False, plan_limit=None,
                 fallback_limit=None, hard_ceiling=100),
            {"limit": None, "configured": False, "source": "unconfigured"},
        ),
        (
            dict(has_subscription=True, entitled=False, plan_limit=None,
                 fallback_limit=50),
            {"limit": None, "configured": False, "source": "unconfigured"},
        ),
        (
            dict(has_subscription=True, entitled=True, plan_limit=None,
                 fallback_limit=50),
            {"limit": None, "configured": True, "source": "unlimited_plan"},
        ),
        (
            dict(has_subscription=True, entitled=False, plan_limit=200,
                 fallback_limit=None, hard_ceiling=150),
            {"limit": 150, "configured": True, "source": "plan_and_platform_ceiling"},
        ),
    ],
)
def test_effective_limit_keeps_plan_default_and_unlimited_states_distinct(args, expected):
    assert resolve_effective_limit(**args) == expected


@pytest.mark.parametrize("value", [0, 1, 5])
def test_default_concurrent_streams_accepts_nonnegative_integers(value):
    update = SettingsUpdate(values={
        "streaming_limits": {"default_concurrent_streams": value},
    })
    assert update.values["streaming_limits"]["default_concurrent_streams"] == value


@pytest.mark.parametrize("value", [-1, 1.5, True, "3"])
def test_default_concurrent_streams_rejects_non_integer_values(value):
    with pytest.raises(ValidationError):
        SettingsUpdate(values={"streaming_limits": {"default_concurrent_streams": value}})


class _Scalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, _statement):
        return _Scalars(self.rows)


def test_streaming_meter_subtracts_completed_and_current_pause_time():
    now = datetime.now(timezone.utc)
    ended = SimpleNamespace(
        org_id="org-a", started_at=now - timedelta(seconds=10),
        ended_at=now - timedelta(seconds=2), paused_ms=2000, paused_at=None,
    )
    paused = SimpleNamespace(
        org_id="org-a", started_at=now - timedelta(seconds=10),
        ended_at=None, paused_ms=1000, paused_at=now - timedelta(seconds=5),
    )
    assert org_svc.streaming_seconds(_Session([ended, paused])) == pytest.approx(10, abs=0.1)


def test_platform_summary_uses_the_same_streaming_meter(monkeypatch):
    from app.services import admin as admin_svc

    monkeypatch.setattr(org_svc, "streaming_seconds", lambda _db: 7200)
    assert admin_svc._streaming_hours(object()) == 2.0


def test_plan_response_schema_preserves_concurrency_entitlement():
    plan = PlanOut(
        id="00000000-0000-0000-0000-000000000001",
        name="Business",
        slug="business",
        currency="USD",
        max_concurrent_streams=5,
        is_active=True,
    )
    assert plan.max_concurrent_streams == 5
