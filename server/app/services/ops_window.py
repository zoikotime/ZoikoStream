"""The Command Center's time window: ONE resolver for every range, and the bucketing and
comparison rules every windowed metric uses.

── WHY ONE RESOLVER ─────────────────────────────────────────────────────────────────────
Each tile used to pick its own window: API health always read the last 15 minutes whatever
range was selected, the sparklines bucketed up to "now" even for a historical custom window,
and the ▲/▼ badges compared the last two sparkline points rather than two periods. A metric
now gets its window from `resolve()` and nowhere else, so two tiles cannot disagree about what
"the last 7 days" means.

── SEMANTICS ────────────────────────────────────────────────────────────────────────────
  live   the last 15 minutes. The CURRENT-STATE tiles (live sessions, at-risk, current
         audience, active incidents) read "now" in every mode; this window is only what the
         windowed parts of a tile (trend, sessions-in-period, request counts) cover.
  1h / 24h / 7d   [now - span, now)
  custom  [from, to), both UTC instants. `to` defaults to now and is clamped to now —
          nothing has been measured after the present, so a window reaching into the future
          is reported as the window actually covered.

Every instant is timezone-aware UTC. The browser displays local time; filtering never runs on
formatted strings.

── COMPARISONS ──────────────────────────────────────────────────────────────────────────
A comparison is always against the PREVIOUS window of equal length ([since - span, since)).
When there is nothing to compare against — no samples, or a previous value of zero, from which
no percentage exists — `delta_pct` is None and the console shows no badge.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

NAMED_RANGES = {
    "live": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}
# Bucket width per named range: minute buckets for the short windows, hourly for a day,
# six-hourly (28 points) for a week.
NAMED_BUCKETS = {"live": 60, "1h": 300, "24h": 3600, "7d": 21600}
# A custom window takes the narrowest of these that keeps the chart at or under MAX_POINTS.
BUCKET_LADDER = (60, 300, 900, 3600, 21600, 86400)
MAX_POINTS = 48
MAX_CUSTOM_SPAN = timedelta(days=92)
# Clock skew between the browser and the server, not a licence to ask about tomorrow.
FUTURE_TOLERANCE = timedelta(minutes=2)


class WindowError(ValueError):
    """An impossible window. The router turns it into a 400 the console can show."""


def utc(dt: datetime | None) -> datetime | None:
    """Aware UTC. A naive value is taken to BE UTC — the documented contract of the API —
    rather than the server's local zone, which would shift every window by the offset."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Window:
    mode: str
    since: datetime
    until: datetime
    bucket_seconds: int

    @property
    def span(self) -> timedelta:
        return self.until - self.since

    @property
    def previous(self) -> "Window":
        """The equal-length window immediately before this one."""
        return Window(self.mode, self.since - self.span, self.since, self.bucket_seconds)

    @property
    def bucket_count(self) -> int:
        secs = self.span.total_seconds()
        return max(1, int(-(-secs // self.bucket_seconds)))   # ceil

    def bucket_starts(self) -> list[datetime]:
        return [self.since + timedelta(seconds=i * self.bucket_seconds)
                for i in range(self.bucket_count)]

    def bucket_index(self, ts: datetime | None) -> int | None:
        ts = utc(ts)
        if ts is None or ts < self.since or ts >= self.until:
            return None
        return min(self.bucket_count - 1,
                   int((ts - self.since).total_seconds() // self.bucket_seconds))

    def as_dict(self) -> dict:
        prev = self.previous
        return {"mode": self.mode, "from": self.since, "to": self.until,
                "bucket_seconds": self.bucket_seconds,
                "previous": {"from": prev.since, "to": prev.until}}


def resolve(mode: str, since: datetime | None = None, until: datetime | None = None,
            now: datetime | None = None) -> Window:
    now = utc(now) or datetime.now(timezone.utc)
    if mode == "custom":
        if since is None:
            raise WindowError("A custom range needs a start ('from') timestamp.")
        since = utc(since)
        until = utc(until) or now
        if since > now + FUTURE_TOLERANCE:
            raise WindowError("A custom range cannot start in the future.")
        until = min(until, now)
        if since >= until:
            raise WindowError("'from' must be before 'to'.")
        if until - since > MAX_CUSTOM_SPAN:
            raise WindowError(f"A custom range can cover at most {MAX_CUSTOM_SPAN.days} days.")
        secs = (until - since).total_seconds()
        bucket = next((b for b in BUCKET_LADDER if secs / b <= MAX_POINTS), BUCKET_LADDER[-1])
        return Window("custom", since, until, bucket)
    if mode not in NAMED_RANGES:
        raise WindowError(f"Unknown range '{mode}'.")
    return Window(mode, now - NAMED_RANGES[mode], now, NAMED_BUCKETS[mode])


def series(window: Window, values: dict[int, float], *, fill: str) -> list[dict]:
    """One point per bucket, labelled with the bucket's start instant.

    fill="zero": every bucket is emitted and an empty one is a MEASURED zero. Only for sources
                 that are authoritative for the whole window (a session table, a request
                 counter that records zero-traffic minutes).
    fill="gap":  empty buckets are dropped. For sampled gauges, where an empty bucket means
                 "nothing was sampled", which is not the same fact as zero.
    """
    out = []
    for i, start in enumerate(window.bucket_starts()):
        if i in values:
            out.append({"label": start.isoformat(), "t": start.isoformat(),
                        "value": round(float(values[i]), 3)})
        elif fill == "zero":
            out.append({"label": start.isoformat(), "t": start.isoformat(), "value": 0.0})
    return out


def compare(current: float | None, previous: float | None) -> dict:
    """Current vs the previous equal-length window. No percentage from nothing."""
    delta = None
    if current is not None and previous not in (None, 0):
        delta = round(100.0 * (current - previous) / previous, 1)
    return {"current": current, "previous": previous, "delta_pct": delta,
            "basis": "previous_equal_window"}
