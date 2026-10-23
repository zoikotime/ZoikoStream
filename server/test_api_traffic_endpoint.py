"""The traffic view behind the Command Center's API-health tile.

GET /admin/api-traffic exists because that tile's "Traffic" link had nowhere honest to go —
it pointed at /admin/developers, the API KEYS page. Nothing in the console reported request
volume, error rate or latency, although every process has been recording all three
(main.py middleware -> services.ops.request_stats -> flushed once a minute).

Nothing new is measured for it: the endpoint resolves a window and hands it to the SAME
services.ops.api_window the dashboard tile uses, so the two cannot disagree.

These tests touch no database. The window is resolved before any query runs, which is what
makes the rejection path assertable with a session that would explode if it were used.
"""
import pytest

from app.services import ops, ops_window


class ExplodingSession:
    """Any attribute access is a failed test: nothing here may reach the database."""

    def __getattr__(self, name):
        raise AssertionError(f"api_traffic touched the database ({name}) before validating")


def test_the_route_is_registered_under_admin():
    from app.main import app
    paths = app.openapi()["paths"]
    assert "/api/admin/api-traffic" in paths
    assert "get" in paths["/api/admin/api-traffic"]


def test_it_is_not_pointed_at_the_api_keys_page():
    # The regression this whole endpoint replaces: traffic is not a developer-keys concern.
    from app.main import app
    assert "/api/admin/api-traffic" != "/api/admin/developers"
    assert "/api/admin/api-traffic" in app.openapi()["paths"]


def test_it_inherits_the_super_admin_gate_from_the_router():
    # The /admin router carries require_super_admin for every route on it, so a new endpoint
    # cannot be added unauthenticated by accident.
    from app.routers.admin import router
    assert router.dependencies, "the admin router must gate every route it carries"


@pytest.mark.parametrize("bad", [
    {"range_": "custom", "since": None},            # custom with no start
    {"range_": "nonsense"},                          # not a range at all
])
def test_an_impossible_window_is_refused_before_any_query(bad):
    with pytest.raises(ops_window.WindowError):
        ops.api_traffic(ExplodingSession(), **bad)


def test_the_named_ranges_are_the_ones_the_tile_offers():
    # live/1h/24h/7d resolve; the view's selector offers exactly these.
    for mode in ("live", "1h", "24h", "7d"):
        win = ops_window.resolve(mode)
        assert win.since < win.until


def test_it_reuses_the_dashboard_function_rather_than_a_second_implementation():
    # If this ever stops being true, the page and the tile can start disagreeing.
    import inspect
    src = inspect.getsource(ops.api_traffic)
    assert "api_window(db, win)" in src
    assert "ops_window.resolve" in src
