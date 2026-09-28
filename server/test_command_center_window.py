"""Command Center: does the selected range reach the right query, and only where it should?

Every DB test runs in ONE uncommitted transaction and measures DELTAS (payload before vs
after adding its rows), so ambient rows in the test database cannot move the assertions and
nothing needs cleaning up - the transaction is rolled back.

  window        1h / 24h / 7d / custom resolve to exact UTC instants; custom is validated;
                comparisons use the previous EQUAL-length window.
  api health    requests, error rate, p95 and the sparkline all come from the same window's
                rows; missing collection is "not measured", never zero.
  current state live sessions, at-risk, active incidents do not change with the range.
  upcoming      forward-looking over its own horizon, filtered in SQL before the limit.
  filters       test mode, region and scope reach the queries they can apply to.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import (
    AnalyticsSnapshot, BroadcastSession, Event, Incident, Organization, PlatformMetric,
    SessionAlert, User,
)
from app.security import create_access_token
from app.services import ops, ops_window
from app.services.ops_window import WindowError, resolve

UTC = timezone.utc


# ── pure: the window resolver ──────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("mode,span,bucket", [
    ("live", timedelta(minutes=15), 60), ("1h", timedelta(hours=1), 300),
    ("24h", timedelta(hours=24), 3600), ("7d", timedelta(days=7), 21600),
])
def test_named_ranges_resolve_to_the_previous_span_ending_now(mode, span, bucket):
    w = resolve(mode, now=NOW)
    assert (w.since, w.until, w.bucket_seconds) == (NOW - span, NOW, bucket)
    assert w.previous.until == w.since and w.previous.span == w.span, "equal-length comparison"


def test_custom_uses_the_selected_instants_and_clamps_the_future():
    w = resolve("custom", NOW - timedelta(days=2), NOW - timedelta(days=1), now=NOW)
    assert (w.since, w.until) == (NOW - timedelta(days=2), NOW - timedelta(days=1))
    assert resolve("custom", NOW - timedelta(hours=3), NOW + timedelta(days=5), now=NOW).until == NOW
    assert resolve("custom", NOW - timedelta(hours=3), now=NOW).until == NOW


def test_timezones_are_normalised_to_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    w = resolve("custom", datetime(2026, 9, 27, 10, 0, tzinfo=ist),
                datetime(2026, 9, 28, 10, 0, tzinfo=ist), now=NOW)
    assert w.since == datetime(2026, 9, 27, 4, 30, tzinfo=UTC)
    naive = resolve("custom", datetime(2026, 9, 27, 10, 0), datetime(2026, 9, 27, 11, 0), now=NOW)
    assert naive.since == datetime(2026, 9, 27, 10, 0, tzinfo=UTC), "naive input is UTC by contract"


@pytest.mark.parametrize("since,until", [
    (None, None),                                            # no start
    (NOW - timedelta(hours=1), NOW - timedelta(hours=2)),    # inverted
    (NOW + timedelta(days=1), NOW + timedelta(days=2)),      # future
    (NOW - timedelta(days=100), NOW),                        # too long
])
def test_invalid_custom_windows_are_refused(since, until):
    with pytest.raises(WindowError):
        resolve("custom", since, until, now=NOW)


def test_comparison_needs_a_real_previous_value():
    assert ops_window.compare(12, 10)["delta_pct"] == 20.0
    assert ops_window.compare(5, 0)["delta_pct"] is None, "no percentage from zero"
    assert ops_window.compare(5, None)["delta_pct"] is None
    assert ops_window.compare(None, 5)["delta_pct"] is None


def test_series_fill_distinguishes_measured_zero_from_gap():
    w = resolve("1h", now=NOW)
    zero = ops_window.series(w, {0: 3}, fill="zero")
    gap = ops_window.series(w, {0: 3}, fill="gap")
    assert len(zero) == w.bucket_count == 12 and zero[1]["value"] == 0.0
    assert len(gap) == 1


def test_request_stats_drain_emits_zero_minutes_and_never_the_current_one():
    stats = ops.RequestStats()
    stats._flushed_through = 99
    stats._buckets.append((101, [3, 1, [], [0] * (len(ops.LATENCY_BOUNDS_MS) + 1)]))
    out = stats.drain(now_minute=103)
    assert [m for m, *_ in out] == [100, 101, 102]
    assert out[0][1] == 0 and out[1][1] == 3 and out[1][2] == 1
    assert stats.drain(now_minute=103) == [], "a minute is flushed once"


def test_p95_is_read_from_the_summed_histogram():
    totals = {"api_lat_le_100": 90, "api_lat_le_250": 9, "api_lat_le_1000": 1}
    assert ops._hist_percentile(totals, 0.95) == (250, False)
    assert ops._hist_percentile({"api_lat_le_inf": 5}, 0.95) == (10000, True)
    assert ops._hist_percentile({}, 0.95) == (None, False)


# ── DB fixtures ────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _now():
    return datetime.now(UTC)


def _admin(db):
    admin = db.query(User).filter(User.role == "super_admin").first()
    if admin is None:
        org = _org(db)
        admin = User(org_id=org.id, full_name="CC Admin", role="super_admin",
                     email=f"cc-{uuid.uuid4().hex[:8]}@t.test",
                     username=f"cc{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(admin)
        db.flush()
    return admin


def _org(db, **kw):
    org = Organization(name=f"cc-{uuid.uuid4().hex[:8]}", status="active", **kw)
    db.add(org)
    db.flush()
    return org


def _event(db, org, admin, **kw):
    kw.setdefault("start_time", _now() + timedelta(hours=4))
    ev = Event(org_id=org.id, created_by=admin.id, title="CC Event", status="scheduled", **kw)
    db.add(ev)
    db.flush()
    return ev


def _session(db, org, admin, *, started, ended=None, status="live"):
    ev = _event(db, org, admin)
    s = BroadcastSession(event_id=ev.id, org_id=org.id, status=status, started_at=started,
                         ended_at=ended)
    db.add(s)
    db.flush()
    return ev, s


def cc(db, **kw):
    return ops.command_center(db, _admin(db), **kw)


# ── API health: one window for every figure ────────────────────────────────────────────

def _api_rows(db, at, requests, errors=0, lat=None):
    db.add(PlatformMetric(name=ops.API_REQUESTS, value=float(requests), recorded_at=at))
    if errors:
        db.add(PlatformMetric(name=ops.API_ERRORS, value=float(errors), recorded_at=at))
    for name, n in (lat or {}).items():
        db.add(PlatformMetric(name=name, value=float(n), recorded_at=at))
    db.flush()


def _minute(dt):
    return dt.replace(second=0, microsecond=0)


def test_api_counts_errors_and_series_follow_the_selected_range(db):
    before = {r: cc(db, range_=r)["kpis"]["api_health"] for r in ("1h", "24h", "7d")}
    now = _minute(_now())
    _api_rows(db, now - timedelta(minutes=30), 10, errors=1)       # in 1h, 24h, 7d
    _api_rows(db, now - timedelta(hours=5), 100, errors=0)         # in 24h, 7d
    _api_rows(db, now - timedelta(days=3), 1000, errors=10)        # in 7d only
    after = {r: cc(db, range_=r)["kpis"]["api_health"] for r in ("1h", "24h", "7d")}

    def delta(r, key):
        return (after[r][key] or 0) - (before[r][key] or 0)

    assert delta("1h", "requests") == 10
    assert delta("24h", "requests") == 110
    assert delta("7d", "requests") == 1110
    assert delta("1h", "errors") == 1 and delta("24h", "errors") == 1 and delta("7d", "errors") == 11
    for r in ("1h", "24h", "7d"):
        # The line is the same rows as the headline count.
        assert sum(p["value"] for p in after[r]["series"]) == after[r]["requests"]
        assert after[r]["value"] == round(100 * after[r]["errors"] / after[r]["requests"], 3)


def test_api_p95_uses_only_the_windows_latencies():
    db = SessionLocal()
    try:
        base = _minute(_now() - timedelta(days=20))
        _api_rows(db, base, 100, lat={"api_lat_le_100": 100})                         # inside
        _api_rows(db, base + timedelta(hours=3), 100, lat={"api_lat_le_10000": 100})  # outside
        inside = ops.api_window(db, resolve("custom", base, base + timedelta(minutes=10)))
        both = ops.api_window(db, resolve("custom", base, base + timedelta(hours=4)))
        assert inside["p95_ms"] == 100 and both["p95_ms"] == 10000
        assert inside["requests"] == 100 and both["requests"] == 200
        # 1 of 10 minutes collected: partial, not a clean measurement.
        assert inside["measurement_state"] == "partial"
    finally:
        db.rollback()
        db.close()


def test_uncollected_api_time_is_not_measured_and_measured_zero_stays_zero(db):
    base = _minute(_now() - timedelta(days=25))
    empty = ops.api_window(db, resolve("custom", base, base + timedelta(minutes=5)))
    assert empty["measurement_state"] == "not_measured"
    assert empty["requests"] is None and empty["value"] is None, "no data is not zero traffic"

    for i in range(5):
        _api_rows(db, base + timedelta(minutes=i), 0)         # collector alive, no traffic
    idle = ops.api_window(db, resolve("custom", base, base + timedelta(minutes=5)))
    assert idle["measurement_state"] == "measured"
    assert idle["requests"] == 0 and idle["value"] is None, "0 requests: no error RATE exists"
    assert len(idle["series"]) == 5 and all(p["value"] == 0 for p in idle["series"])


def test_flush_writes_minute_rows_that_the_window_reads(db, monkeypatch):
    minute = int((_now() - timedelta(days=22)).timestamp() // 60)
    hist = [0] * (len(ops.LATENCY_BOUNDS_MS) + 1)
    hist[4] = 7                                                  # <= 100 ms
    stats = ops.RequestStats()
    monkeypatch.setattr(stats, "drain", lambda: [(minute, 7, 2, hist)])
    written = []

    class _Keep:
        def __call__(self):
            return self

        def add_all(self, rows):
            written.extend(rows)
            db.add_all(rows)

        def commit(self):
            db.flush()

        def rollback(self):
            pass

        def close(self):
            pass

    assert ops.flush_request_stats(_Keep(), stats) == 3
    at = datetime.fromtimestamp(minute * 60, tz=UTC)
    got = ops.api_window(db, resolve("custom", at, at + timedelta(minutes=1)))
    assert (got["requests"], got["errors"], got["p95_ms"]) == (7, 2, 100)


# ── current-state tiles ignore the range; windowed parts follow it ─────────────────────

def test_live_sessions_are_current_and_the_period_figure_is_windowed(db):
    admin = _admin(db)
    before = {r: cc(db, range_=r)["kpis"]["live_sessions"] for r in ("1h", "24h", "7d")}
    org = _org(db)
    _session(db, org, admin, started=_now() - timedelta(days=10))          # live for 10 days
    _session(db, org, admin, started=_now() - timedelta(days=3, hours=1),
             ended=_now() - timedelta(days=3), status="ended")             # 3 days ago
    after = {r: cc(db, range_=r)["kpis"]["live_sessions"] for r in ("1h", "24h", "7d")}
    for r in ("1h", "24h", "7d"):
        assert after[r]["value"] - before[r]["value"] == 1, "live NOW, whatever the range"
        assert after[r]["semantics"] == "current"
    assert after["1h"]["in_period"] - before["1h"]["in_period"] == 1
    assert after["24h"]["in_period"] - before["24h"]["in_period"] == 1
    assert after["7d"]["in_period"] - before["7d"]["in_period"] == 2


def test_at_risk_and_attention_are_operational_not_date_filtered(db):
    admin = _admin(db)
    org = _org(db)
    ev, _s = _session(db, org, admin, started=_now() - timedelta(days=9))
    db.add(SessionAlert(event_id=ev.id, org_id=org.id, severity="critical", stage="deliver",
                        issue="Viewers report no playback", opened_at=_now() - timedelta(days=8)))
    db.flush()
    for r in ("live", "1h"):
        out = cc(db, range_=r)
        assert any(a["event_id"] == str(ev.id) for a in out["attention"]), r
        assert out["kpis"]["at_risk_sessions"]["total"] >= 1


def test_active_incidents_are_all_open_ones_regardless_of_start(db):
    old = Incident(ref=f"INC-{uuid.uuid4().hex[:8]}", title="Old, still open", severity="sev2",
                   status="open", started_at=_now() - timedelta(days=60))
    db.add(old)
    for _ in range(8):   # a busy day of resolved incidents must not push it off
        db.add(Incident(ref=f"INC-{uuid.uuid4().hex[:8]}", title="Resolved", status="resolved",
                        started_at=_now() - timedelta(hours=3),
                        resolved_at=_now() - timedelta(hours=2)))
    db.flush()
    one_h = cc(db, range_="1h")["incident_summary"]
    day = cc(db, range_="24h")["incident_summary"]
    assert str(old.id) in {i["id"] for i in one_h["active"]}
    assert str(old.id) in {i["id"] for i in day["active"]}
    assert day["resolved_count"] - one_h["resolved_count"] >= 8, "resolved counts follow the window"


def test_upcoming_is_forward_looking_and_filtered_before_the_limit(db):
    admin = _admin(db)
    org = _org(db)
    for i in range(30):   # a run of standard events ahead of the high-impact one
        _event(db, org, admin, impact="standard", start_time=_now() + timedelta(minutes=10 + i))
    high = _event(db, org, admin, impact="high", start_time=_now() + timedelta(days=2))
    past = _event(db, org, admin, impact="high", start_time=_now() - timedelta(days=2))
    far = _event(db, org, admin, impact="unrepeatable", start_time=_now() + timedelta(days=12))
    for r in ("1h", "7d"):
        ids = {e["id"] for e in cc(db, range_=r)["upcoming_events"]}
        assert str(high.id) in ids, "must not be crowded out by standard events"
        assert str(past.id) not in ids and str(far.id) not in ids
    up = cc(db, range_="7d")["upcoming"]
    assert up["semantics"] == "upcoming" and up["to"] - up["from"] == ops.UPCOMING_HORIZON


# ── filters ────────────────────────────────────────────────────────────────────────────

def test_test_mode_toggle_changes_counts(db):
    admin = _admin(db)
    base_ex = cc(db, range_="1h")["kpis"]["live_sessions"]["value"]
    base_in = cc(db, range_="1h", include_test=True)["kpis"]["live_sessions"]["value"]
    _session(db, _org(db, is_test=True), admin, started=_now() - timedelta(minutes=5))
    assert cc(db, range_="1h")["kpis"]["live_sessions"]["value"] == base_ex
    assert cc(db, range_="1h", include_test=True)["kpis"]["live_sessions"]["value"] == base_in + 1


def test_region_filter_reaches_sessions_and_incidents(db):
    admin = _admin(db)
    before = {rg: cc(db, range_="1h", region=rg) for rg in ("eu", "na")}
    _session(db, _org(db, region="EU West (Ireland)"), admin, started=_now() - timedelta(minutes=5))
    na_inc = Incident(ref=f"INC-{uuid.uuid4().hex[:8]}", title="NA only", status="open", region="na")
    glob = Incident(ref=f"INC-{uuid.uuid4().hex[:8]}", title="Global", status="open", region=None)
    db.add_all([na_inc, glob])
    db.flush()
    eu, na = cc(db, range_="1h", region="eu"), cc(db, range_="1h", region="na")
    assert eu["kpis"]["live_sessions"]["value"] == before["eu"]["kpis"]["live_sessions"]["value"] + 1
    assert na["kpis"]["live_sessions"]["value"] == before["na"]["kpis"]["live_sessions"]["value"]
    eu_ids = {i["id"] for i in eu["incident_summary"]["active"]}
    assert str(glob.id) in eu_ids and str(na_inc.id) not in eu_ids
    assert eu["kpis"]["api_health"]["applies"]["region"] is False, "no region dimension"


def test_scope_filter_reaches_health_incidents_and_attention(db):
    inc = Incident(ref=f"INC-{uuid.uuid4().hex[:8]}", title="Delivery", status="open",
                   stage="deliver")
    db.add(inc)
    db.flush()
    core, live, both = (cc(db, range_="1h", scope=s) for s in ("core", "live", "core_live"))
    assert str(inc.id) not in {i["id"] for i in core["incident_summary"]["active"]}
    assert str(inc.id) in {i["id"] for i in live["incident_summary"]["active"]}
    assert core["kpis"]["platform_health"]["total"] < both["kpis"]["platform_health"]["total"]
    assert core["kpis"]["platform_health"]["series"] == [], "history is platform-wide only"


# ── audience: measured zero, not sampled, and the peak actually summed ─────────────────

def test_audience_zero_vs_not_sampled(db):
    admin = _admin(db)
    org = _org(db, region="SA East (Sao Paulo)")
    quiet = cc(db, range_="live", region="sa")["kpis"]["concurrent_audience"]
    if quiet["current"] == 0:
        assert quiet["measurement_state"] == "measured"
    _session(db, org, admin, started=_now() - timedelta(minutes=5))   # live, never sampled
    out = cc(db, range_="live", region="sa")["kpis"]["concurrent_audience"]
    assert out["current"] is None and out["measurement_state"] == "not_sampled"


def test_audience_peak_sums_events_sampled_in_the_same_tick(db):
    """The regression: per-event snapshots carry slightly different created_at values, and
    the old peak grouped by exact timestamp - reporting the largest single event."""
    admin = _admin(db)
    org = _org(db, region="SA East (Sao Paulo)")
    t = _now().replace(second=0, microsecond=0) - timedelta(minutes=20)
    ev1, _ = _session(db, org, admin, started=t - timedelta(minutes=1))
    ev2, _ = _session(db, org, admin, started=t - timedelta(minutes=1))
    db.add_all([
        AnalyticsSnapshot(event_id=ev1.id, org_id=org.id, viewers=300, created_at=t + timedelta(seconds=1)),
        AnalyticsSnapshot(event_id=ev2.id, org_id=org.id, viewers=200, created_at=t + timedelta(seconds=2)),
    ])
    db.flush()
    win = resolve("custom", t - timedelta(minutes=1), t + timedelta(minutes=1))
    orgs = ops.OrgFilter(db, "sa", include_test=True)
    slots, _sampled = ops._audience_slots(db, win, orgs)
    assert max(v for _, v in slots) == 500


# ── HTTP ───────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def http_admin():
    """A COMMITTED super admin: the TestClient authenticates through its own session."""
    s = SessionLocal()
    org = Organization(name=f"cc-http-{uuid.uuid4().hex[:8]}", status="active")
    s.add(org)
    s.flush()
    admin = User(org_id=org.id, full_name="CC HTTP", role="super_admin",
                 email=f"cchttp-{uuid.uuid4().hex[:8]}@t.test",
                 username=f"cchttp{uuid.uuid4().hex[:8]}", password_hash="x")
    s.add(admin)
    s.commit()
    try:
        yield admin
    finally:
        s.delete(admin)
        s.delete(org)
        s.commit()
        s.close()


def test_http_contract_and_invalid_custom_range(http_admin):
    c = TestClient(m.app)
    c.headers["Authorization"] = f"Bearer {create_access_token(http_admin, remember=False)}"
    ok = c.get("/api/admin/command-center", params={"range": "7d"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["range"]["mode"] == "7d" and body["range"]["bucket_seconds"] == 21600
    for key, sem in (("live_sessions", "current"), ("at_risk_sessions", "current"),
                     ("api_health", "window"), ("concurrent_audience", "window")):
        assert body["kpis"][key]["semantics"] == sem
    since = datetime.fromisoformat(body["range"]["from"])
    until = datetime.fromisoformat(body["range"]["to"])
    assert until - since == timedelta(days=7)
    for params in ({"range": "custom"},
                   {"range": "custom", "from": "2026-09-20T10:00:00Z", "to": "2026-09-19T10:00:00Z"},
                   {"range": "custom", "from": "2099-01-01T00:00:00Z"}):
        bad = c.get("/api/admin/command-center", params=params)
        assert bad.status_code == 400, params
