"""Storage ceilings cap entitled plan allowances; they do not grant storage.

Non-entitled organizations now have a zero storage allowance regardless of stored platform
defaults or a configured platform ceiling. An entitled plan's finite allowance may be tightened
by the configured ceiling; an entitled unlimited plan may be capped by it. The API continues to
report plan and effective enforcement values separately.

No database: storage_ceiling_gb takes a session only to read one row, and the rest is source.
"""
import inspect

from app.services import platform_settings


class FakeSession:
    """Answers db.get(PlatformSetting, key) from a dict."""

    def __init__(self, rows=None):
        self.rows = rows or {}

    def get(self, _model, key):
        return self.rows.get(key)


class Row:
    def __init__(self, value):
        self.value = value


# ── the ceiling is read, never invented ─────────────────────────────────────────────────

def test_an_unset_ceiling_is_none_rather_than_a_number():
    # The regression this file exists for: an absent setting must not acquire a default.
    assert platform_settings.storage_ceiling_gb(FakeSession()) is None


def test_a_setting_row_with_no_value_is_still_none():
    assert platform_settings.storage_ceiling_gb(FakeSession({"storage_limits": Row(None)})) is None


def test_a_setting_row_without_max_gb_is_still_none():
    assert platform_settings.storage_ceiling_gb(FakeSession({"storage_limits": Row({})})) is None


def test_the_configured_value_is_what_is_returned():
    db = FakeSession({"storage_limits": Row({"max_gb": 5000})})
    assert platform_settings.storage_ceiling_gb(db) == 5000.0


def test_any_configured_value_is_honoured_not_just_the_familiar_one():
    # 5000 is not special to the code — it is whatever the administrator typed.
    for configured in (0, 1, 250, 5000, 99999):
        db = FakeSession({"storage_limits": Row({"max_gb": configured})})
        assert platform_settings.storage_ceiling_gb(db) == float(configured)


def test_no_storage_fallback_number_is_written_into_the_resolver():
    src = inspect.getsource(platform_settings.storage_ceiling_gb)
    assert "5000" not in src
    # `or` on the value would turn a configured 0 into "unset", which is a different policy
    # than the one above and must not creep in unnoticed.
    assert "max_gb\") or" not in src


def test_the_ceiling_is_an_editable_setting_not_a_constant():
    from app.routers.admin import EDITABLE_SETTING_KEYS
    assert "storage_limits" in EDITABLE_SETTING_KEYS


# ── entitlement and ceiling stay separate in what is reported ───────────────────────────

def test_the_reporting_payload_keeps_them_in_different_fields():
    from app.services import org as org_svc
    src = inspect.getsource(org_svc.org_overview) if hasattr(org_svc, "org_overview") else inspect.getsource(org_svc)
    # `limit` carries the plan's entitlement; `enforced_limit` carries what actually refuses.
    # Collapsing them would report a platform ceiling as an organization's allowance.
    assert '"enforced_limit"' in src
    assert '"quota_state"' in src


def test_non_storage_quotas_without_a_quota_can_report_not_applicable():
    from app.services import org as org_svc
    src = inspect.getsource(org_svc)
    # The three "no number" states are distinct on purpose; not_applicable means there is no
    # plan or configured default rather than an unlimited allowance. Storage without
    # entitlement is separately represented as an explicit zero quota.
    assert '"not_applicable"' in src
    assert '"unlimited"' in src


def test_the_enforcement_resolver_still_refuses_to_invent_a_plan():
    from app.services import org as org_svc
    src = inspect.getsource(org_svc.enforcement_plan)
    # Returning None for an organization with no subscription row is the documented
    # behaviour; a default plan here would hand an allowance to tenants entitled to none.
    assert "return lapsed.plan if lapsed is not None else None" in src
