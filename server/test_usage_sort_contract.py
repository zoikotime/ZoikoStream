"""The ordering contract behind Usage & Entitlements' sort control.

A report said "Most storage used" did not sort. Traced end to end, every link is correct:

    the control sends      sort=storage
    the route accepts      ^(name|-name|storage)$
    services/admin         Organization.storage_used_gb.desc().nulls_last(), name.asc(), id.asc()
    and applies it         base.order_by(*order, ...).offset(...).limit(...)   <- ORDER BY first
    the page renders       the rows in the order they arrived, with no second sort

What the sort reads is `Organization.storage_used_gb`, a running total incremented in
services/broadcast when egress reports a finished file — and only then:

    if info and info.size:
        ... org.storage_used_gb = ... + delta_bytes / (1024 ** 3)

A recording whose size the provider never reported adds nothing to it. That is the same
evidence gap the Media console labels "unverified", and the usage card names when it says
"Recording storage (known sizes)". With every organization sitting at 0, the descending sort
ties and the name tiebreak puts Acme first — correct behaviour that reads as a broken sort.

These tests pin the ordering contract so it cannot regress into the bug it was reported as.
They touch no database: the ordering is read from the service's source, and the accepted sort
values from the route's own signature.
"""
import inspect

import pytest


def _usage_overview_source():
    from app.services import admin as admin_svc
    return inspect.getsource(admin_svc.usage_overview)


# ── what the sort orders by ─────────────────────────────────────────────────────────────

def test_storage_sorts_on_the_measured_column_descending():
    src = _usage_overview_source()
    assert "Organization.storage_used_gb.desc()" in src, \
        "storage must order by the measured counter, descending"


def test_storage_sorting_puts_unknown_values_last():
    assert "nulls_last()" in _usage_overview_source()


def test_storage_ties_break_on_name_so_the_order_is_stable():
    src = _usage_overview_source()
    # With every organization at 0 this tiebreak is the ENTIRE visible order, which is why a
    # correct sort can look like no sort at all.
    assert "Organization.name.asc()" in src


def test_the_sort_does_not_read_a_quota_or_a_formatted_string():
    src = _usage_overview_source()
    for wrong in ("max_storage_gb", "enforced_limit", "quota_state"):
        assert f"{wrong}.desc()" not in src, f"ordering must not be derived from {wrong}"


# ── ordering happens before paging ──────────────────────────────────────────────────────

def test_order_by_is_applied_before_offset_and_limit():
    src = _usage_overview_source()
    order_at = src.index(".order_by(")
    offset_at = src.index(".offset(")
    limit_at = src.index(".limit(")
    assert order_at < offset_at < limit_at, \
        "sorting a page after slicing it would order only that page"


def test_the_ordering_is_applied_in_sql_not_in_python():
    src = _usage_overview_source()
    # A Python sort would only ever see the page that was already fetched.
    assert "sorted(" not in src
    assert ".sort(" not in src


# ── what the route will accept ──────────────────────────────────────────────────────────

def _sort_param():
    from app.main import app
    params = app.openapi()["paths"]["/api/admin/usage"]["get"]["parameters"]
    return [p for p in params if p["name"] == "sort"][0]


@pytest.mark.parametrize("value", ["name", "-name", "storage"])
def test_the_three_offered_sorts_are_accepted(value):
    import re
    pattern = _sort_param()["schema"].get("pattern") or _sort_param()["schema"]["anyOf"][0]["pattern"]
    assert re.fullmatch(pattern, value)


@pytest.mark.parametrize("value", ["storage_desc", "-storage", "bandwidth", "name; DROP TABLE", ""])
def test_anything_else_is_refused_at_the_boundary(value):
    # A value the control might plausibly send but the service cannot map must be a 422,
    # never a silent fall back to alphabetical.
    import re
    pattern = _sort_param()["schema"].get("pattern") or _sort_param()["schema"]["anyOf"][0]["pattern"]
    assert not re.fullmatch(pattern, value)


def test_an_unmapped_sort_would_fall_back_to_name_not_to_nothing():
    src = _usage_overview_source()
    # .get(sort, (Organization.name.asc(),)) — a default exists, so there is always an
    # ORDER BY and paging stays deterministic.
    assert ".get(sort," in src


# ── what the sorted column actually counts ──────────────────────────────────────────────

def test_storage_is_only_counted_when_the_provider_reported_a_size():
    from app.services import broadcast
    src = inspect.getsource(broadcast)
    assert "if info and info.size:" in src, \
        "the counter's guard is why organizations with unverified recordings read 0 GB"


def test_the_counter_accumulates_a_delta_rather_than_overwriting():
    from app.services import broadcast
    src = inspect.getsource(broadcast)
    assert "delta_bytes = info.size - (r.size_bytes or 0)" in src
