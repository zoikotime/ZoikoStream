"""The 5,000 GB storage ceiling is configuration, not a default — pinned so it stays that way.

A report arrived claiming organizations with no entitled plan were "receiving an unintended
default storage ceiling of 5,000 GB". They are not. Nothing in this repository contains that
number as a storage fallback:

    platform_settings.storage_ceiling_gb() reads the "storage_limits" PlatformSetting row and
    returns None when it is absent. "storage_limits" is in routers.admin.EDITABLE_SETTING_KEYS,
    so 5000 is a value an administrator entered in Settings -> Storage & Streaming Limits.

What the console shows for such an organization is already two separate facts, and says so:

    quota_state = "not_applicable"   <- there is no entitlement
    enforced_limit = <ceiling>       <- what actually refuses an upload

services.org.enforcement_plan draws that line deliberately, and
test_entitlement_enforcement.py already covers it — including that no plan is fabricated for
an organization with no subscription row, that a lapsed subscription keeps its own plan as the
ceiling, and that one tenant's plan never governs another's.

These tests cover the part that suite does not: that the ceiling remains CONFIGURATION, and
that the reporting payload keeps entitlement and ceiling in separate fields. The failure they
are written against is someone "fixing" an absent ceiling by giving it a default — which is
precisely the bug the report believed was already here, and which would grant an allowance to
organizations entitled to none.

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


def test_an_organization_with_no_plan_reports_a_not_applicable_quota():
    from app.services import org as org_svc
    src = inspect.getsource(org_svc)
    # The three "no number" states are distinct on purpose; not_applicable is the one that
    # says "nothing is entitled" rather than "unlimited".
    assert '"not_applicable"' in src
    assert '"unlimited"' in src


def test_the_enforcement_resolver_still_refuses_to_invent_a_plan():
    from app.services import org as org_svc
    src = inspect.getsource(org_svc.enforcement_plan)
    # Returning None for an organization with no subscription row is the documented
    # behaviour; a default plan here would hand an allowance to tenants entitled to none.
    assert "return lapsed.plan if lapsed is not None else None" in src
