"""Analytics presentation: report periods, caching, exports and the organization alert feed.

crud/analytics.py does the counting; this module decides what a "monthly report" spans, keeps the
expensive roll-ups off the database on every page load, and turns a result set into a file.

Exports are stdlib only:
  * CSV   — the `csv` module.
  * XLSX  — `zipfile` plus five small XML parts. An .xlsx IS a zip of XML, and writing one by hand
            is ~60 lines against a whole new deployment dependency (openpyxl) for a spreadsheet
            with no formulas, charts or styling. Inline strings, so there is no shared-string table
            to keep consistent.
  * PDF   — deliberately NOT generated here. See PDF_NOTE.

ponytail: the cache is an in-process dict with a TTL. Multiple workers each keep their own copy,
which for a 60-second analytics window is correct behaviour rather than a compromise — the numbers
are already up to one sample interval old. Move it to Redis (services/bus) if a deployment ever
needs the invalidation to be shared.
"""

from __future__ import annotations

import calendar
import csv
import io
import logging
import re
import time
import zipfile
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# ── what this platform cannot measure ─────────────────────────────────────────
# Collected in one place so every screen can print the same reason, and so adding a source later
# means deleting an entry here rather than hunting for the copy that says otherwise.
UNAVAILABLE = {
    "viewer_geography": "Country and city breakdowns need a GeoIP lookup, which is not integrated.",
    "devices_historical": ("Device, browser and platform mix is reported live from presence and is "
                           "not persisted, so it is available during an event and not afterwards."),
    "bandwidth": ("Egress bandwidth is not metered by this platform — the SFU or CDN invoice is "
                  "the source of truth for it."),
    "encoder_history": ("Bitrate, packet loss, RTT and frame rate are published by the encoder over "
                        "the live socket and shown in the host console. They are not stored."),
    "traffic_sources": ("Referrer tracking is not implemented; there is no source for how a viewer "
                        "arrived at an event."),
    "audience_rating": "No survey or rating feature exists, so speakers are not scored by attendees.",
    "certificates": ("Attendance is recorded, but no certificate template or issuing flow exists, "
                     "so no certificates have been earned."),
}

PDF_NOTE = (
    "PDF export renders in the browser's own print engine from the report view (Print → Save as "
    "PDF). Generating one server-side would mean shipping a PDF toolchain to lay out the same "
    "charts the page already draws, and the browser's output is better. CSV, Excel and JSON are "
    "produced server-side."
)


# ── report periods ────────────────────────────────────────────────────────────

PERIODS = ("today", "daily", "weekly", "monthly", "quarterly", "yearly", "custom")
# Which trend bucket suits each period. A yearly report bucketed by day is 365 unreadable columns.
PERIOD_BUCKET = {"today": "hour", "daily": "hour", "weekly": "day", "monthly": "day",
                 "quarterly": "week", "yearly": "month", "custom": "day"}


def period_window(period: str, *, now: datetime | None = None,
                  start: datetime | None = None, end: datetime | None = None) -> tuple[datetime, datetime, str]:
    """(start, end, bucket) for a named period.

    Periods are CALENDAR-aligned, not rolling: "monthly" means this month to date, because that is
    what a finance or marketing reader compares against last month. A rolling 30 days answers a
    different question and is available as `custom`.
    """
    now = now or datetime.now(timezone.utc)
    bucket = PERIOD_BUCKET.get(period, "day")

    if period == "custom":
        if start and end:
            span_days = max(1, (end - start).days)
            # Pick a bucket that yields a readable number of columns for whatever range was asked
            # for, rather than forcing the caller to know the vocabulary.
            bucket = ("hour" if span_days <= 2 else "day" if span_days <= 62
                      else "week" if span_days <= 400 else "month")
            return start, end, bucket
        return now - timedelta(days=30), now, "day"

    if period in ("today", "daily"):
        begin = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "weekly":
        begin = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
    elif period == "monthly":
        begin = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "quarterly":
        first_month = 3 * ((now.month - 1) // 3) + 1
        begin = now.replace(month=first_month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "yearly":
        begin = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        begin = now - timedelta(days=30)
    return begin, now, bucket


def previous_window(start: datetime, end: datetime, period: str) -> tuple[datetime, datetime]:
    """The comparable preceding window.

    Calendar periods step back by a CALENDAR unit, not by the elapsed length: on the 3rd of the
    month, "last month" is the whole of last month, not the 3 days before this month began.
    Comparing 3 days against 31 is the classic way a dashboard reports a 90% collapse every time a
    month rolls over.
    """
    if period == "monthly":
        last_day_prev = start - timedelta(days=1)
        return last_day_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0), start
    if period == "yearly":
        return start.replace(year=start.year - 1), start
    if period == "quarterly":
        month = start.month - 3
        year = start.year if month >= 1 else start.year - 1
        month = month if month >= 1 else month + 12
        return start.replace(year=year, month=month), start
    span = end - start
    return start - span, start


def label_for(period: str, start: datetime, end: datetime) -> str:
    if period in ("today", "daily"):
        return f"{start:%d %b %Y}"
    if period == "weekly":
        return f"Week of {start:%d %b %Y}"
    if period == "monthly":
        return f"{start:%B %Y}"
    if period == "quarterly":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    if period == "yearly":
        return str(start.year)
    return f"{start:%d %b %Y} – {end:%d %b %Y}"


# ── cache ─────────────────────────────────────────────────────────────────────

CACHE_TTL_SECONDS = 60
_cache: dict[tuple, tuple[float, object]] = {}
# A ceiling so a tenant hammering distinct filter combinations cannot grow this without bound.
MAX_CACHE_ENTRIES = 500


def cached(key: tuple, build, *, ttl: int = CACHE_TTL_SECONDS):
    """Memoize one roll-up for `ttl` seconds.

    60 seconds is chosen against the data, not for comfort: the sampler writes every 15 seconds and
    registrations trickle in, so a minute-old organization roll-up is indistinguishable from a fresh
    one to a reader — while an executive dashboard left open on a wall costs one query a minute
    instead of one per refresh.

    Deliberately NOT applied to live_now (that is the real-time screen) or to anything a mutation
    just changed.
    """
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = build()
    if len(_cache) >= MAX_CACHE_ENTRIES:
        # Cheapest useful eviction: drop everything already expired, and if that frees nothing,
        # clear the lot. An LRU here would be a heap and a lock for a 60-second cache.
        for k in [k for k, (expires, _) in _cache.items() if expires <= now]:
            _cache.pop(k, None)
        if len(_cache) >= MAX_CACHE_ENTRIES:
            _cache.clear()
    _cache[key] = (now + ttl, value)
    return value


def invalidate(org_id=None) -> int:
    """Drop cached roll-ups. Called after a mutation that would otherwise show stale figures."""
    if org_id is None:
        count = len(_cache)
        _cache.clear()
        return count
    doomed = [k for k in _cache if k and str(k[0]) == str(org_id)]
    for k in doomed:
        _cache.pop(k, None)
    return len(doomed)


# ── alerts ────────────────────────────────────────────────────────────────────

def alerts(*, storage_stats: dict, policy: dict, failed_recordings: int,
           live_events: int, last_report_at: str | None = None) -> list[dict]:
    """The organization notification feed the brief asks for, DERIVED rather than delivered.

    Every item here is a fact about the current state, evaluated when read. There is no scheduler
    and no notification table in this stack, so a stored "Weekly report ready" row would be a
    notification nobody sent — instead the feed says what is true now, and the one alert with a real
    trigger point (a recording that failed) also sends mail from the failure path itself
    (services/broadcast._notify_recording_failed).
    """
    out = []
    percent = storage_stats.get("percent_used")
    threshold = policy.get("alert_storage_percent", 85)
    if percent is not None and percent >= threshold:
        out.append({
            "key": "storage_almost_full",
            "level": "critical" if percent >= 95 else "warning",
            "title": "Storage almost full",
            "detail": (f"{percent}% of the {_gb(storage_stats.get('quota_bytes'))} quota is used. "
                       f"The recycle bin holds {_gb(storage_stats.get('recycle_bin_bytes'))}."),
            "action": "Empty the recycle bin or raise the quota",
        })
    if failed_recordings:
        out.append({
            "key": "recording_failed",
            "level": "critical",
            "title": f"{failed_recordings} recording{'s' if failed_recordings > 1 else ''} failed",
            "detail": ("No file was produced. A capture can be retried from the host console while "
                       "the broadcast is still live."),
            "action": "Open the recording queue",
        })
    if live_events:
        out.append({
            "key": "live_now",
            "level": "info",
            "title": f"{live_events} event{'s' if live_events > 1 else ''} on air",
            "detail": "Real-time figures are on the live analytics dashboard.",
            "action": "Open live analytics",
        })
    out.append({
        "key": "analytics_ready",
        "level": "info",
        "title": "Analytics up to date",
        "detail": (f"Figures refresh every {CACHE_TTL_SECONDS}s and are computed from live rows. "
                   "Reports are generated on demand — there is no scheduled emailer in this "
                   "deployment, so nothing is delivered unprompted."),
        "action": "Generate a report",
    })
    return out


def _gb(value) -> str:
    return f"{(int(value or 0) / 1024 ** 3):.1f} GB"


# ── exports ───────────────────────────────────────────────────────────────────

EXPORT_FORMATS = ("csv", "xlsx", "json")


def safe_filename(stem: str, extension: str) -> str:
    """A filename safe for a Content-Disposition header and for Windows, macOS and Linux."""
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", (stem or "report")).strip("-")[:80] or "report"
    return f"{clean}.{extension}"


def to_csv(columns: list[tuple[str, str]], rows: list[dict]) -> bytes:
    """UTF-8 CSV with a BOM.

    The BOM is not decoration: Excel on Windows opens a BOM-less UTF-8 CSV in the system codepage,
    which turns every non-ASCII event title into mojibake. `\r\n` for the same reason.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([header for _key, header in columns])
    for row in rows:
        writer.writerow([_flat(row.get(key)) for key, _header in columns])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def _flat(value):
    """Scalars for a cell. A list becomes a joined string; None becomes empty, not "None"."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}={v}" for k, v in value.items())
    return value


def _xml_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _cell_ref(col: int, row: int) -> str:
    """A1-style reference. Column 27 is AA, which is the part a naive chr() gets wrong."""
    letters = ""
    while col > 0:
        col, remainder = divmod(col - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row}"


# Characters Excel refuses inside a worksheet cell. Stripped rather than escaped: they have no
# XML representation at all, and one of them makes the whole file unopenable.
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def to_xlsx(columns: list[tuple[str, str]], rows: list[dict], *, sheet: str = "Report") -> bytes:
    """A minimal, valid .xlsx.

    ponytail: hand-rolled OOXML instead of adding openpyxl for a plain grid. Numbers are written as
    numeric cells (so Excel sums them) and everything else as an inline string (so there is no
    shared-string table to keep in step). Header row is frozen — the one piece of formatting worth
    the four lines, because a 500-row export is unreadable without it.

    If this ever needs formulas, charts, merged cells or number formats, that is the moment to add
    the library. It does not need them today.
    """
    sheet_name = _xml_escape(re.sub(r"[\[\]:*?/\\]", "-", sheet or "Report")[:31] or "Report")

    body = []
    header_cells = "".join(
        f'<c r="{_cell_ref(i + 1, 1)}" t="inlineStr"><is><t xml:space="preserve">'
        f"{_xml_escape(_ILLEGAL_XML.sub('', header))}</t></is></c>"
        for i, (_key, header) in enumerate(columns)
    )
    body.append(f'<row r="1">{header_cells}</row>')

    for index, row in enumerate(rows, start=2):
        cells = []
        for col, (key, _header) in enumerate(columns, start=1):
            value = _flat(row.get(key))
            ref = _cell_ref(col, index)
            if value == "":
                continue  # an empty cell is best expressed by not writing one
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                text = _xml_escape(_ILLEGAL_XML.sub("", str(value))[:32000])
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>')
        body.append(f'<row r="{index}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.worksheet+xml"/></Types>'
    )

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        # [Content_Types].xml must be the FIRST entry — some readers (including older Excel) give up
        # on the archive if it is not.
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return out.getvalue()


# Column sets per export scope: (row key, human header). Defined here rather than in the router so
# CSV and XLSX cannot drift apart, and so the header row is reviewable in one place.
EXPORT_COLUMNS = {
    "events": [
        ("title", "Event"), ("status", "Status"), ("category", "Category"),
        ("start_time", "Start"), ("location", "Location"), ("visibility", "Visibility"),
        ("registrations", "Registrations"), ("attended", "Attended"),
        ("attendance_rate", "Attendance rate %"), ("unique_attendees", "Unique attendees"),
        ("peak_viewers", "Peak viewers"), ("avg_concurrent", "Avg concurrent"),
        ("watch_seconds", "Watch time (s)"), ("avg_watch_seconds", "Avg watch (s)"),
        ("messages", "Chat messages"), ("questions", "Questions"),
        ("questions_answered", "Questions answered"), ("reactions", "Reactions"),
        ("hands", "Hands raised"), ("broadcast_seconds", "Broadcast (s)"),
        ("recordings", "Recordings"), ("replay_views", "Replay views"),
        ("replay_downloads", "Replay downloads"), ("engagement_score", "Engagement score"),
        ("tags", "Tags"),
    ],
    "speakers": [
        ("name", "Speaker"), ("email", "Email"), ("department", "Department"),
        ("events", "Events"), ("speaking_seconds", "Speaking time (s)"),
        ("events_measured", "Events measured"), ("questions_assigned", "Questions assigned"),
        ("questions_answered", "Questions answered"), ("answer_rate", "Answer rate %"),
        ("avg_attendance", "Avg attendance"), ("peak_attendance", "Peak attendance"),
        ("drop_off_percent", "Drop-off %"),
    ],
    "attendees": [
        ("name", "Attendee"), ("email", "Email"), ("department", "Department"),
        ("registrations", "Registrations"), ("attended", "Attended"),
        ("attendance_rate", "Attendance rate %"), ("watch_seconds", "Watch time (s)"),
        ("sessions_joined", "Sessions joined"), ("questions_asked", "Questions asked"),
        ("messages_sent", "Messages sent"), ("bookmarks", "Bookmarks"),
        ("recording_marks", "Recording notes"), ("last_seen", "Last seen"),
    ],
    "trends": [
        ("period", "Period"), ("events", "Events"), ("completed", "Completed"),
        ("cancelled", "Cancelled"), ("registrations", "Registrations"), ("attended", "Attended"),
        ("watch_seconds", "Watch time (s)"), ("peak_viewers", "Peak viewers"),
        ("messages", "Messages"), ("reactions", "Reactions"),
    ],
    "recordings": [
        ("title", "Recording"), ("event_title", "Event"), ("status", "Status"),
        ("category", "Category"), ("stopped_at", "Recorded"), ("duration_ms", "Duration (ms)"),
        ("size_bytes", "Size (bytes)"), ("quality", "Quality"), ("visibility", "Visibility"),
        ("view_count", "Views"), ("download_count", "Downloads"),
        ("has_transcript", "Transcript"), ("tags", "Tags"),
    ],
}


def demo() -> None:
    """Self-check: period arithmetic, the cache, and the two file writers. No database."""
    import json

    # ── periods ──
    now = datetime(2026, 8, 4, 15, 30, tzinfo=timezone.utc)   # a Tuesday
    start, end, bucket = period_window("monthly", now=now)
    assert (start.day, start.month, start.hour) == (1, 8, 0) and end == now and bucket == "day"
    start, _, _ = period_window("weekly", now=now)
    assert start.day == 3 and start.weekday() == 0, "a week starts on Monday"
    start, _, _ = period_window("quarterly", now=now)
    assert start.month == 7, "August sits in the quarter beginning July"
    start, _, _ = period_window("yearly", now=now)
    assert (start.month, start.day) == (1, 1)
    start, _, _ = period_window("today", now=now)
    assert (start.hour, start.minute) == (0, 0)

    # The month-rollover trap: comparing 4 days against a full previous month.
    m_start, m_end, _ = period_window("monthly", now=now)
    p_start, p_end = previous_window(m_start, m_end, "monthly")
    assert (p_start.year, p_start.month, p_start.day) == (2026, 7, 1)
    assert p_end == m_start, "the previous month must run up to this month's first day"
    assert (p_end - p_start).days == 31, "a whole calendar month, not the elapsed 4 days"
    y_start, y_end = previous_window(datetime(2026, 1, 1, tzinfo=timezone.utc), now, "yearly")
    assert y_start.year == 2025
    q_start, _ = previous_window(datetime(2026, 1, 1, tzinfo=timezone.utc), now, "quarterly")
    assert (q_start.year, q_start.month) == (2025, 10), "quarter before Q1 is the prior year's Q4"

    # Custom ranges choose a readable bucket.
    assert period_window("custom", start=now - timedelta(days=1), end=now)[2] == "hour"
    assert period_window("custom", start=now - timedelta(days=45), end=now)[2] == "day"
    assert period_window("custom", start=now - timedelta(days=200), end=now)[2] == "week"
    assert period_window("custom", start=now - timedelta(days=900), end=now)[2] == "month"
    assert label_for("monthly", m_start, m_end) == "August 2026"
    assert label_for("quarterly", datetime(2026, 7, 1, tzinfo=timezone.utc), now) == "Q3 2026"

    # ── cache ──
    invalidate()
    calls = []
    build = lambda: (calls.append(1), {"n": len(calls)})[1]   # noqa: E731
    key = ("org1", "overview")
    assert cached(key, build)["n"] == 1
    assert cached(key, build)["n"] == 1, "second read must hit the cache"
    assert len(calls) == 1
    cached(("org2", "overview"), build)
    assert invalidate("org1") == 1 and invalidate("org1") == 0
    assert cached(("org2", "overview"), build)["n"] == 2, "org2's entry must survive org1's flush"
    # An expired entry rebuilds.
    _cache[key] = (time.monotonic() - 1, {"n": "stale"})
    assert cached(key, build) != {"n": "stale"}
    invalidate()

    # ── CSV ──
    columns = [("title", "Event"), ("attended", "Attended"), ("tags", "Tags"),
               ("rate", "Rate %"), ("live", "Live")]
    rows = [
        {"title": 'Q3 "Town Hall", Berlin', "attended": 42, "tags": ["sales", "emea"],
         "rate": 87.5, "live": True},
        {"title": "Café münchen", "attended": 0, "tags": [], "rate": None, "live": False},
    ]
    blob = to_csv(columns, rows)
    assert blob.startswith(b"\xef\xbb\xbf"), "Excel needs the BOM to read UTF-8"
    text = blob.decode("utf-8-sig")
    assert text.splitlines()[0] == "Event,Attended,Tags,Rate %,Live"
    assert '"Q3 ""Town Hall"", Berlin"' in text, "quotes and commas must be escaped"
    assert "sales, emea" in text and "Café münchen" in text
    assert text.rstrip().endswith("no"), "None renders empty and False renders 'no'"
    assert ",,\r\n" not in text.splitlines()[0]
    # Round-trips through a real CSV reader.
    parsed = list(csv.reader(io.StringIO(text)))
    assert len(parsed) == 3 and parsed[1][0] == 'Q3 "Town Hall", Berlin' and parsed[2][3] == ""

    # ── XLSX ──
    book = to_xlsx(columns, rows, sheet="Events/2026")
    assert book[:2] == b"PK", "an xlsx is a zip"
    with zipfile.ZipFile(io.BytesIO(book)) as zf:
        names = zf.namelist()
        assert names[0] == "[Content_Types].xml", "content types must be the first entry"
        for required in ("_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
                         "xl/worksheets/sheet1.xml"):
            assert required in names, required
        sheet = zf.read("xl/worksheets/sheet1.xml").decode()
        # Numbers are numeric cells so Excel can sum them; text is an inline string.
        assert "<c r=\"B2\"><v>42</v></c>" in sheet
        assert "<c r=\"D2\"><v>87.5</v></c>" in sheet
        assert 'r="A2" t="inlineStr"' in sheet
        assert "Q3 &quot;Town Hall&quot;, Berlin" in sheet, "XML escaping"
        assert "Café münchen" in sheet
        assert 'r="D3"' not in sheet, "a None cell is omitted, not written empty"
        assert '<pane ySplit="1"' in sheet, "header row frozen"
        assert "Events-2026" in zf.read("xl/workbook.xml").decode(), "sheet name sanitised"

    # Column letters past Z.
    assert _cell_ref(1, 1) == "A1" and _cell_ref(26, 3) == "Z3"
    assert _cell_ref(27, 1) == "AA1" and _cell_ref(53, 9) == "BA9"
    # A control character would make the workbook unopenable, so it is stripped.
    nasty = to_xlsx([("a", "A")], [{"a": "bad\x00char"}])
    with zipfile.ZipFile(io.BytesIO(nasty)) as zf:
        assert "badchar" in zf.read("xl/worksheets/sheet1.xml").decode()

    # ── filenames + alerts ──
    assert safe_filename("ZoikoStream analytics: Q3/2026", "csv") == "ZoikoStream-analytics-Q3-2026.csv"
    assert safe_filename("", "xlsx") == "report.xlsx"

    feed = alerts(
        storage_stats={"percent_used": 96.0, "quota_bytes": 100 * 1024 ** 3,
                       "recycle_bin_bytes": 5 * 1024 ** 3},
        policy={"alert_storage_percent": 85}, failed_recordings=2, live_events=1)
    keys = [a["key"] for a in feed]
    assert keys[0] == "storage_almost_full" and feed[0]["level"] == "critical"
    assert "recording_failed" in keys and "live_now" in keys and "analytics_ready" in keys
    quiet = alerts(storage_stats={"percent_used": 10.0, "quota_bytes": 1},
                   policy={"alert_storage_percent": 85}, failed_recordings=0, live_events=0)
    assert [a["key"] for a in quiet] == ["analytics_ready"]
    # No quota configured must not raise a storage alarm on a None comparison.
    assert alerts(storage_stats={"percent_used": None}, policy={}, failed_recordings=0,
                  live_events=0)[0]["key"] == "analytics_ready"

    # Every export scope's columns are (key, header) pairs.
    for scope, cols in EXPORT_COLUMNS.items():
        assert cols and all(isinstance(c, tuple) and len(c) == 2 for c in cols), scope
    assert json.dumps(UNAVAILABLE)   # serialisable, since it ships to the client

    print("ok  analytics period / cache / export self-check")


if __name__ == "__main__":
    demo()
