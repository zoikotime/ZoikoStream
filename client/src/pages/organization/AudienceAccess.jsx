import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  FiUserCheck, FiRefreshCw, FiUsers, FiCalendar, FiTrendingUp, FiDownload, FiShield, FiEye, FiRadio,
  FiLock, FiAlertTriangle, FiChevronRight,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, XAxis, YAxis } from "recharts";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";
import { ChartTooltip } from "../../ui/charts";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import DataTable from "../../components/admin/DataTable";
import Panel from "../../components/admin/Panel";
import { DarkCard, IconChip, KpiRow, KpiTile, Panel as ConsolePanel, PanelLink } from "../../components/organization/console/ConsoleUI";
import { useOrgScope } from "../../components/organization/orgScope";
import FactGrid from "../../components/organization/FactGrid";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import { displayStatusOf, fmtDateTime, statusMeta, visLabel } from "../../data/events";
import { downloadCsv } from "../../utils/export";

// Audience Access — who has registered, who may watch, and how full each event is.
//
// Registration is the only audience fact this platform stores per person, and it is stored per
// EVENT, so the page is organised that way: capacity and gating per event, with the attendee
// analytics the analytics service does produce alongside it.
//
// The per-event registration counts come from GET /events, which now resolves them for the
// whole page in one grouped query over event_registrations (crud/event.registration_counts).
// The comment here used to claim that was already true; it was not — no `registered_count`
// existed anywhere in the backend, so every event printed an em dash and the Registrations
// KPI printed one too, for an organization that really did have registrations.
//
// The KPI row does NOT add up the rows on screen. It reads GET /organization/audience-summary,
// which counts over the whole selected window in SQL — otherwise the cards would describe the
// current page ("3 events") rather than the organization.
//
// The Attendance panel on the right reads GET /organization/audience-attendance
// (services/org.py::audience_attendance). Not every figure there is a true measurement —
// see that function's own docstring — `unique_attendees_estimated`/`avg_watch_minutes_estimated`
// flag which ones are derived from 15s concurrent-viewer sampling rather than counted. The
// panel renders those caveats rather than hiding them.
//
// Registration → attendance and the Audience insights panel read GET
// /organization/audience-insights (server/app/services/audience.py), which counts recorded
// viewers: one per event and viewer admitted to the live stream, never staff or hosts, never a
// reconnect. Geography is only what viewers chose to give at registration; device and player
// classes are detected by the server. Nothing here is estimated or filled in — an empty window
// says so.
const RANGES = [
  ["7d", "7 days"],
  ["30d", "30 days"],
  ["90d", "90 days"],
];

const RANGE_LABEL = { "7d": "7 days", "30d": "30 days", "90d": "90 days" };

const fillTone = (pct) => {
  if (pct == null) return "neutral";
  if (pct >= 95) return "danger";
  if (pct >= 80) return "warning";
  return "success";
};

// Days per range key, so the events list can be filtered by the SAME window the KPI row and
// the attendance panel use. The range buttons used to reload only the attendance panel, which
// left the table and the four cards showing every event regardless of the selection.
const RANGE_DAYS = { "7d": 7, "30d": 30, "90d": 90 };
// The table's orders, as `${key}:${dir}` over SORT_FIELD's keys (start_time is the default).
const SORT_OPTIONS = [
  ["start_time:desc", "Newest start"],
  ["start_time:asc", "Oldest start"],
  ["title:asc", "Event name (A–Z)"],
  ["visibility:asc", "Audience (visibility)"],
  ["registered:desc", "Most registered"],
  ["pct:desc", "Largest capacity"],
];

const PAGE_SIZE = 20;               // inside the API's le=100 cap

// DataTable names a column by the row field it renders; the API names sortable fields in its
// own vocabulary (crud/event._EVENT_SORTS) and falls back to created_at for anything else.
// Sending a column key straight through would silently sort by creation date while the header
// claimed otherwise — so only mapped columns are marked sortable.
const SORT_FIELD = {
  title: "title",
  visibility: "visibility",
  registered: "registered",
  pct: "registration_limit",
};

const GEO_TOP = 5;   // countries listed by name; the rest share one "Other countries" row
const viewersText = (n) => `${n} ${n === 1 ? "viewer" : "viewers"}`;

// Registration → attendance, from the insights payload. "Not applicable" when nobody registered
// in the window — a rate over nothing is not 0%.
function attendanceFact(insights) {
  const label = "Registration → attendance";
  if (insights.loading) return { label, value: null, reason: "Loading" };
  const ra = insights.data?.registration_attendance;
  if (!ra) return { label, value: null, reason: "Couldn't load audience insights" };
  if (!ra.applicable || !ra.registered) {
    return { label, value: "Not applicable", tone: CONSOLE.faint, detail: "No registrations in this window" };
  }
  return { label, value: `${ra.rate}%`, detail: `${ra.attended} of ${ra.registered} registered viewers attended` };
}

function geographyRows(geography, known) {
  const top = geography.slice(0, GEO_TOP).map((g) => ({
    key: g.country_code, label: g.country_name, viewers: g.viewers, percentage: g.percentage ?? 0,
  }));
  const rest = geography.slice(GEO_TOP);
  if (rest.length) {
    const viewers = rest.reduce((sum, g) => sum + g.viewers, 0);
    top.push({
      key: "other", label: `Other countries (${rest.length})`, viewers,
      percentage: known ? Math.round((100 * viewers) / known) : 0,
    });
  }
  return top;
}

// Label, viewers and share, with a bar carrying the same share for anyone scanning.
function ShareList({ rows, label }) {
  return (
    <ul className="space-y-2.5" aria-label={label}>
      {rows.map((r) => (
        <li key={r.key}>
          <div className="flex items-baseline justify-between gap-3 text-[13px]">
            <span className={cx("truncate", CONSOLE.heading)}>{r.label}</span>
            <span className={cx("shrink-0 tabular-nums", CONSOLE.muted)}>
              {viewersText(r.viewers)} · {r.percentage}%
            </span>
          </div>
          <span className="mt-1 block h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-white/10" aria-hidden="true">
            <span className="block h-full rounded-full bg-emerald-500" style={{ width: `${Math.min(r.percentage, 100)}%` }} />
          </span>
        </li>
      ))}
    </ul>
  );
}

const shareLine = (rows) => rows.map((r) => `${r.label} ${r.percentage}%`).join(" · ");

function InsightBlock({ title, children }) {
  return (
    <section className="min-w-0" aria-label={title}>
      <h3 className={cx("mb-3 text-[13px] font-semibold", CONSOLE.heading)}>{title}</h3>
      {children}
    </section>
  );
}

function Empty({ children }) {
  return <p className={cx("text-[13px]", CONSOLE.faint)}>{children}</p>;
}

function AudienceInsights({ data }) {
  const geography = data.geography || [];
  const devices = (data.device_mix || []).map((d) => ({
    key: d.device_type, label: d.label, viewers: d.viewers, percentage: d.percentage ?? 0,
  }));
  const breakdown = data.device_breakdown || {};
  const blocked = data.blocked_join_attempts || { total: 0, reasons: [] };
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <InsightBlock title="Audience geography">
        {geography.length ? (
          <>
            <ShareList rows={geographyRows(geography, data.geography_known)} label="Viewers by country" />
            {data.geography_unknown > 0 && (
              <p className={cx("mt-3 text-[12px]", CONSOLE.faint)}>
                {viewersText(data.geography_unknown)} did not share a country.
              </p>
            )}
          </>
        ) : (
          <Empty>No geography data collected yet</Empty>
        )}
      </InsightBlock>

      <InsightBlock title="Device and player mix">
        {devices.length ? (
          <>
            <ShareList rows={devices} label="Viewers by device" />
            <dl className={cx("mt-3 space-y-1 text-[12px]", CONSOLE.faint)}>
              {[
                ["Browsers", breakdown.browsers],
                ["Operating systems", breakdown.operating_systems],
                ["Players", breakdown.clients],
              ].filter(([, rows]) => rows?.length).map(([name, rows]) => (
                <div key={name}>
                  <dt className="inline">{name}: </dt>
                  <dd className="inline">{shareLine(rows)}</dd>
                </div>
              ))}
            </dl>
          </>
        ) : (
          <Empty>No viewer device data yet</Empty>
        )}
      </InsightBlock>

      <InsightBlock title="Blocked join attempts">
        <p className={cx("text-[20px] font-semibold tabular-nums", CONSOLE.heading)}>{blocked.total}</p>
        {blocked.reasons.length ? (
          <ul className="mt-2 space-y-1.5" aria-label="Blocked join attempts by reason">
            {blocked.reasons.map((r) => (
              <li key={r.reason} className="flex items-baseline justify-between gap-3 text-[13px]">
                <span className={cx("truncate", CONSOLE.muted)}>{r.label}</span>
                <span className={cx("shrink-0 tabular-nums", CONSOLE.heading)}>{r.count}</span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No blocked join attempts in this window</Empty>
        )}
      </InsightBlock>
    </div>
  );
}

// Bucket labels for the activity series (UTC, as the server buckets it).
const BUCKET_LABEL = {
  hour: (d) => d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }),
  day: (d) => d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }),
  week: (d) => d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }),
};

// ORG-03 "Playback activity": sessions that began in each UTC day (hour / week for the
// shortest and longest windows), straight from the server's series — every bucket a real
// count, empty ones zero. Nothing drawn when nothing was reported.
function PlaybackActivity({ qoe }) {
  const total = qoe.total?.sessions ?? 0;
  const series = qoe.series;
  const label = BUCKET_LABEL[series?.bucket] || BUCKET_LABEL.day;
  const data = (series?.points || []).map((p) => ({ label: label(new Date(p.start)), sessions: p.sessions }));
  const peak = data.reduce((best, p) => (p.sessions > (best?.sessions ?? -1) ? p : best), null);
  return (
    <>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[12px]">
        <span className={cx("inline-flex items-center gap-1.5", CONSOLE.body)}>
          <span aria-hidden="true" className="h-2 w-2 rounded-full bg-violet-500" /> Playback sessions
        </span>
        <span className={cx("font-semibold tabular-nums", CONSOLE.heading)}>{total.toLocaleString()} total sessions</span>
      </div>
      {!series || total === 0 ? (
        <div className={cx("rounded-xl border border-dashed px-4 py-8 text-center text-[12.5px]", CONSOLE.divider)}>
          <p className={CONSOLE.faint}>No playback sessions were reported in this window.</p>
          <p className={cx("mt-1.5 text-[11.5px]", CONSOLE.muted)}>
            Player telemetry is recorded only when viewers start video playback. Event registrations represent sign-ups and do not indicate attendance or started sessions.
          </p>
        </div>
      ) : (
        <div
          role="img"
          aria-label={`Playback sessions per ${series.bucket} (UTC): ${total} in total${peak ? `, highest ${peak.sessions} on ${peak.label}` : ""}.`}
          style={{ height: 200 }}
        >
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
              <defs>
                <linearGradient id="zk-aud-sessions" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-slate-100 dark:text-white/10" vertical={false} />
              <XAxis dataKey="label" fontSize={11} tickLine={false} axisLine={false} stroke="currentColor" className="text-slate-400 dark:text-neutral-500" minTickGap={24} />
              <YAxis allowDecimals={false} fontSize={11} tickLine={false} axisLine={false} stroke="currentColor" className="text-slate-400 dark:text-neutral-500" width="auto" />
              <ChartTooltip formatter={(v) => [v.toLocaleString(), "Sessions"]} />
              <Area type="monotone" dataKey="sessions" name="Sessions" stroke="#8b5cf6" strokeWidth={2} fill="url(#zk-aud-sessions)" dot={false} activeDot={{ r: 4 }} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
      <p className={cx("mt-2 text-[12px] tabular-nums", CONSOLE.muted)}>
        Live {(qoe.live?.sessions ?? 0).toLocaleString()} · Replay {(qoe.replay?.sessions ?? 0).toLocaleString()} · UTC {series?.bucket === "hour" ? "hours" : series?.bucket === "week" ? "weeks" : "days"}
      </p>
    </>
  );
}

export default function AudienceAccess() {
  const [range, setRange] = useState("30d");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState({ key: "start_time", dir: "desc" });
  const navigate = useNavigate();

  // Debounced, so a request isn't fired per keystroke — same shape the Members and Identity
  // & Access consoles use.
  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), 300);
    return () => clearTimeout(t);
  }, [qInput]);

  const sinceIso = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - (RANGE_DAYS[range] ?? 30));
    return d.toISOString();
  }, [range]);

  // Server-side throughout: the window, the search and the page are all decided by the API,
  // so the table is never a client-side slice of an unbounded fetch.
  const events = useApi(() =>
    api
      .get("/events", {
        params: {
          page,
          page_size: PAGE_SIZE,
          q: q || undefined,
          date_from: sinceIso,
          sort_by: (sort && SORT_FIELD[sort.key]) || "start_time",
          order: sort?.dir || "desc",
        },
      })
      .then((r) => r.data)
  );
  // Dataset-wide counters for the KPI row, counted in SQL over the same window.
  const summary = useApi(() =>
    api.get("/organization/audience-summary", { params: { range } }).then((r) => r.data)
  );
  const attendance = useApi(() =>
    api.get("/organization/audience-attendance", { params: { range } }).then((r) => r.data)
  );
  const a = attendance.data || {};
  // Same window as the rest of the page, across the organization's events. (The endpoint also
  // takes an event_id for one event's figures.)
  const insights = useApi(() =>
    api.get("/organization/audience-insights", { params: { range } }).then((r) => r.data)
  );
  // Playback sessions (ORG-03 "Playback sessions — not unique people"): what viewers' players
  // reported, live and replay, over the same window as the rest of the page — the summary also
  // carries the per-day series and each event's session count.
  const qoeWindow = range;
  const rangeLabel = RANGE_LABEL[range] || `${range} days`;
  const qoe = useApi(() =>
    api.get("/organization/playback/qoe", { params: { window: qoeWindow } }).then((r) => r.data)
  );
  const { state: shell } = useOrgScope();

  // useApi fetches on mount and on reload() only, so every server-decided input refetches
  // explicitly. Guarded-render refetch is this repo's idiom in place of an effect.
  const inputs = `${range}|${q}|${page}|${sort?.key}|${sort?.dir}`;
  const [lastInputs, setLastInputs] = useState(inputs);
  if (inputs !== lastInputs) {
    setLastInputs(inputs);
    events.reload();
    if (!lastInputs.startsWith(range)) { summary.reload(); attendance.reload(); insights.reload(); qoe.reload(); }
  }

  // Narrowing the result must return to its first page.
  const resetPage = (apply) => { setPage(1); apply(); };

  const rows = useMemo(() => {
    const list = Array.isArray(events.data) ? events.data : events.data?.items || [];
    return list.map((ev) => {
      // ?? not ||: a counted 0 is a real answer and must not fall through to null. Only a
      // response that omits the field entirely (an older API build) is "unknown".
      const registered = ev.registered_count ?? null;
      const limit = ev.registration_limit ?? null;
      const pct = registered != null && limit ? Math.round((registered / limit) * 100) : null;
      return { ...ev, registered, limit, pct };
    });
  }, [events.data]);

  const total = events.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Counted server-side over the whole window. `null` when the summary call failed — the
  // cards then render an em dash rather than a zero, because a failed request is not a
  // measurement of nothing.
  const s = summary.data;
  const totals = {
    events: s ? s.events : null,
    gated: s ? s.registration_required : null,
    registrations: s ? s.registrations : null,
    atCapacity: s ? s.at_capacity : null,
    capped: s ? s.events_with_capacity : null,
  };
  const UNKNOWN = "—";
  const kpi = (v) => (v == null ? UNKNOWN : v);


  const columns = [
    {
      key: "title",
      header: "Event",
      sortable: true,
      className: "min-w-[13rem]",
      render: (ev) => (
        <div className="min-w-0">
          <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>
            {ev.title || "Untitled event"}
          </p>
          <p className={cx("truncate text-[11px]", CONSOLE.faint)}>
            {ev.start_time ? fmtDateTime(ev.start_time) : "Unscheduled"}
          </p>
          {/* Who may watch, and on what condition (no sort key: the API has no ordering for
              the registration flag). */}
          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
            <Badge tone="brand" size="sm">{visLabel(ev.visibility)}</Badge>
            {ev.registration_required ? (
              <Badge tone="info" size="sm">Required</Badge>
            ) : (
              <span className={CONSOLE.faint}>Open</span>
            )}
          </p>
        </div>
      ),
    },
    {
      key: "lifecycle",
      header: "Lifecycle",
      render: (ev) => {
        const m = statusMeta(displayStatusOf(ev));
        return <Badge tone={m.tone} dot={m.pulse} size="sm">{m.label}</Badge>;
      },
    },
    {
      // Player sessions for this event over the page's window (the QoE summary's by_event):
      // sessions, not people. "—" only when the player figures could not be read.
      key: "sessions",
      header: "Sessions",
      align: "right",
      render: (ev) => (
        <span className={cx("text-[13px] tabular-nums", type.mono, qoe.data ? CONSOLE.heading : CONSOLE.faint)}>
          {qoe.data?.by_event ? (qoe.data.by_event[ev.id] ?? 0).toLocaleString() : "—"}
        </span>
      ),
    },
    {
      // How many registered, against what capacity: the count on top, the fill beneath.
      key: "registered",
      header: "Registrations",
      align: "right",
      sortable: true,
      sortValue: (ev) => ev.registered ?? -1,
      render: (ev) => (
        <div className="flex flex-col items-end gap-1">
          <span className={cx("text-[13px]", type.mono, ev.registered ? CONSOLE.heading : CONSOLE.faint)}>
            {ev.registered ?? "—"}
            {ev.limit ? <span className={CONSOLE.faint}> / {ev.limit}</span> : null}
          </span>
          {ev.limit == null ? (
            // Genuinely uncapped: registration_limit is NULL on the event. Not a stand-in for
            // a figure we failed to fetch — an event with a limit always shows the number.
            <span className={cx("text-[11.5px]", CONSOLE.faint)} title="No capacity limit set">
              Uncapped
            </span>
          ) : ev.pct == null ? (
            // Capped, but this response did not carry a registered count, so the fill is
            // unknown. The configured capacity is still a fact and is still shown.
            <span className={cx("text-[11.5px]", type.mono, CONSOLE.faint)}>capacity {ev.limit}</span>
          ) : (
            <span className="inline-flex items-center gap-2">
              {/* Bar carries the same verdict as the number, for anyone scanning rather than
                  reading. aria-hidden because the adjacent text already states it. */}
              <span
                className="h-1.5 w-12 overflow-hidden rounded-full bg-slate-200 dark:bg-white/10"
                aria-hidden="true"
              >
                <span
                  className={cx(
                    "block h-full rounded-full",
                    ev.pct >= 95 ? "bg-rose-500" : ev.pct >= 80 ? "bg-amber-500" : "bg-green-500"
                  )}
                  style={{ width: `${Math.min(ev.pct, 100)}%` }}
                />
              </span>
              <Badge tone={fillTone(ev.pct)} size="sm">
                {ev.pct}%
              </Badge>
              {/* The configured capacity itself, so the column answers "how many?" and not
                  only "how full?". */}
              <span className={cx("text-[11.5px]", type.mono, CONSOLE.faint)}>of {ev.limit}</span>
            </span>
          )}
        </div>
      ),
    },
    {
      // Where this event's access is reviewed and changed: its own workspace.
      key: "destination",
      header: "Destination",
      render: (ev) => (
        <Link
          to={`/organization/events/${ev.id}/access`}
          onClick={(e) => e.stopPropagation()}
          aria-label={`Open the access workspace for ${ev.title || "this event"}`}
          className={cx("inline-flex items-center gap-0.5 whitespace-nowrap rounded text-[12.5px] font-semibold", CONSOLE.link, focusRing)}
        >
          Open workspace <FiChevronRight aria-hidden="true" />
        </Link>
      ),
    },
  ];

  const exportRows = () =>
    downloadCsv("audience-access.csv", rows, [
      ["Event", (r) => r.title || ""],
      ["Starts", (r) => r.start_time || ""],
      ["Visibility", (r) => visLabel(r.visibility)],
      ["Registration required", (r) => (r.registration_required ? "yes" : "no")],
      ["Registered", (r) => r.registered ?? ""],
      ["Capacity", (r) => r.limit ?? ""],
    ]);

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        breadcrumbs={[["Organization", "/organization/manage"], ["Audience & Access", "/organization/audience"], ["Audience overview"]]}
        eyebrow="Organization console / Audience governance"
        title="Audience & access"
        subtitle="Understand viewing activity and manage event access safely — without assuming every viewer has an account."
        actions={
          <>
            <div
              className={cx("inline-flex rounded-lg p-0.5", CONSOLE.segment)}
              role="group"
              aria-label="Analytics range"
            >
              {RANGES.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => resetPage(() => setRange(value))}
                  aria-pressed={range === value}
                  className={cx(
                    "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors duration-150 motion-reduce:transition-none",
                    range === value ? CONSOLE.segmentOn : CONSOLE.segmentOff
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <ConsoleButton variant="secondary" leftIcon={FiDownload} onClick={exportRows} disabled={rows.length === 0}>
              Export
            </ConsoleButton>
            <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={events.reload}>
              Refresh
            </ConsoleButton>
            {/* Each event's access is reviewed in its own workspace; this takes you to the
                table whose rows open it. (Event access has its own rail entry.) */}
            <ConsoleButton leftIcon={FiShield} href="#access-by-event">
              Review event access
            </ConsoleButton>
          </>
        }
      />

      {events.error && (
        <OrganizationErrorState error={events.error} onRetry={events.reload} title="Couldn't load events" />
      )}

      {summary.error && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200">
          {/* A failed summary must not read as "zero registrations". */}
          <span>Couldn&apos;t load the audience totals. The table below is unaffected.</span>
          <ConsoleButton size="sm" variant="secondary" onClick={summary.reload}>Retry</ConsoleButton>
        </div>
      )}

      <KpiRow>
        {/* Counted in SQL across the selected window — not summed from the page of rows
            below, which would describe the page instead of the organization. */}
        <KpiTile icon={FiCalendar} tone="violet" label="Events in scope" value={summary.loading ? "…" : kpi(totals.events)}
          caption={`Events in the selected ${rangeLabel}`} />
        <KpiTile icon={FiEye} tone="sky" label="Playback sessions"
          value={qoe.loading && !qoe.data ? "…" : qoe.data ? (qoe.data.total?.sessions ?? 0).toLocaleString() : UNKNOWN}
          caption={qoe.error ? "Playback telemetry unavailable" : `Reported in the last ${rangeLabel} · not unique people`} />
        <KpiTile icon={FiUsers} tone="amber" label="Registrations" value={summary.loading ? "…" : kpi(totals.registrations)}
          caption="Across events in window · does not imply attendance" />
        <KpiTile icon={FiRadio} tone="pink" label="Live events" value={shell?.badges ? shell.badges.live_events ?? 0 : UNKNOWN}
          caption="Viewer conditions monitored" />
      </KpiRow>

      <div className="flex gap-3 rounded-2xl border border-violet-200/80 bg-violet-50/70 px-4 py-3.5 text-[12.5px] dark:border-violet-400/25 dark:bg-violet-500/[0.08]">
        <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-white text-violet-600 dark:bg-white/10 dark:text-violet-300"><FiShield /></span>
        <div>
          <p className="text-[14px] font-semibold text-violet-900 dark:text-violet-100">Viewer-access policy decisions remain open</p>
          <p className={CONSOLE.body}>
            Authentication, authorization, registration and playback are separate. Access is set per event from its visibility and
            registration rules, while playback telemetry measures media delivered to viewers who actually attended.
          </p>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_20rem] 2xl:grid-cols-[minmax(0,1fr)_26rem]">
        <div className="min-w-0 space-y-6">
          <ConsolePanel
            title="Playback activity"
            subtitle={`Playback sessions reported in the last ${rangeLabel} — not a count of identified viewers or registrants`}
            tag="Player-reported"
            tagTone="violet"
          >
            {qoe.error ? (
              <p className={cx("text-sm", CONSOLE.muted)}>Playback telemetry couldn&apos;t be read.</p>
            ) : !qoe.data ? (
              <p className={cx("text-sm", CONSOLE.faint)}>Loading…</p>
            ) : (
              <PlaybackActivity qoe={qoe.data} />
            )}
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <p className={cx("text-[11.5px]", CONSOLE.faint)}>
                Playback sessions require a viewer to start media in a player. Registrations do not automatically start playback sessions.
              </p>
              <PanelLink to="/organization/analytics">Open analytics</PanelLink>
            </div>
          </ConsolePanel>

      <Panel
          id="access-by-event"
          title="Access by event"
          // No `count`: Panel renders it inline with the title, and the table's own
          // "Showing 1–20 of N" footer already states the total — from the same number.
          flush
        >
          <div className="flex flex-wrap items-center gap-3 px-4 pt-3">
            <input
              value={qInput}
              onChange={(e) => resetPage(() => setQInput(e.target.value))}
              placeholder="Search events…"
              aria-label="Search events"
              className={cx(CONSOLE.search, "min-w-[12rem] flex-1")}
            />
            {/* Every server-side order the table offers, including the two whose columns now
                share a cell (Audience, Capacity) and so have no header of their own. Shares
                its state with the sortable headers. */}
            <select
              value={SORT_OPTIONS.some(([v]) => v === `${sort?.key}:${sort?.dir}`) ? `${sort.key}:${sort.dir}` : "column"}
              onChange={(e) => {
                const [key, dir] = e.target.value.split(":");
                resetPage(() => setSort({ key, dir }));
              }}
              aria-label="Sort events"
              className={cx(CONSOLE.select, "h-9 text-[13px]")}
            >
              {SORT_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              {!SORT_OPTIONS.some(([v]) => v === `${sort?.key}:${sort?.dir}`) && <option value="column" disabled>Sort: by column</option>}
            </select>
            {/* Two more window-wide counts, kept beside the table they describe. */}
            <div className={cx(CONSOLE.inset, "px-3 py-1.5")}>
              <p className={cx("text-[11px]", CONSOLE.faint)}>Registration required</p>
              <p className={cx("text-[15px] font-semibold tabular-nums", CONSOLE.heading)}>{summary.loading ? "…" : kpi(totals.gated)}</p>
            </div>
            <div className={cx(CONSOLE.inset, "px-3 py-1.5")}>
              <p className={cx("text-[11px]", CONSOLE.faint)}>At capacity</p>
              <p className={cx("text-[15px] font-semibold tabular-nums", CONSOLE.heading)}>{summary.loading ? "…" : kpi(totals.atCapacity)}</p>
              {totals.capped === 0 && <p className={cx("text-[10.5px]", CONSOLE.faint)}>No event has a capacity set</p>}
            </div>
          </div>
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(ev) => ev.id}
            loading={events.loading}
            minWidth={600}
            // Server-driven: DataTable's own search/sort/paging would only ever act on the
            // page the API already returned, which is how "oldest" comes to mean "oldest of
            // the twenty on screen".
            pageSize={PAGE_SIZE}
            serverSort={sort}
            onSortChange={(next) => resetPage(() => setSort(next || { key: "start_time", dir: "desc" }))}
            serverPage={page}
            serverPageCount={pageCount}
            serverTotal={total}
            onPageChange={setPage}
            onRowClick={(ev) => navigate(`/organization/events/${ev.id}/access`)}
            empty={{
              icon: FiUserCheck,
              title: "No events yet",
              description:
                "Audience access is defined per event. Create one to set its visibility, registration rule and capacity.",
              action: (
                <ConsoleButton href="/organization/events" size="sm">
                  Go to Live Events
                </ConsoleButton>
              ),
            }}
          />
      </Panel>
        </div>

        <div className="space-y-6">
          <DarkCard>
            <p className="inline-flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.16em] text-white/70">
              <FiShield aria-hidden="true" /> Access governance
            </p>
            <p className="mt-2 text-[20px] font-bold leading-snug">Access rules belong to each event.</p>
            <p className="mt-2 text-[13px] text-white/70">
              Validate the viewer experience, link behaviour and permissions in the event&apos;s Audience Access before
              publishing changes.
            </p>
            <ul className="mt-4 space-y-2 border-t border-white/10 pt-3 text-[12.5px] text-white/85">
              <li className="flex items-center gap-2"><FiLock aria-hidden="true" /> Registration is not a default gate</li>
              <li className="flex items-center gap-2"><FiShield aria-hidden="true" /> Link and invitation changes are audited</li>
            </ul>
            <p className="mt-3 text-[10.5px] font-bold uppercase tracking-[0.14em] text-white/60">View-only summary · policy-gated controls</p>
          </DarkCard>

          <ConsolePanel title="Needs attention" subtitle="Event-level access signals from this window"
            action={<span className="rounded-md bg-amber-50 px-2 py-0.5 text-[12px] font-bold text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
              {rows.filter((r) => r.pct != null && r.pct >= 80).length + (insights.data?.blocked_join_attempts?.total ? 1 : 0)}
            </span>}>
            <ul>
              {rows.filter((r) => r.pct != null && r.pct >= 80).slice(0, 3).map((r) => (
                <li key={r.id} className={cx("flex items-start gap-3 border-t py-3 first:border-t-0", CONSOLE.divider)}>
                  <IconChip icon={FiAlertTriangle} tone="amber" size="sm" />
                  <div className="min-w-0">
                    <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{r.title || "Untitled event"}</p>
                    <p className={cx("text-[12px]", CONSOLE.faint)}>Registration at {r.pct}% of capacity.</p>
                    <PanelLink to={`/organization/events/${r.id}/access`}>Review in Event Workspace</PanelLink>
                  </div>
                </li>
              ))}
              {insights.data?.blocked_join_attempts?.total > 0 && (
                <li className={cx("flex items-start gap-3 border-t py-3 first:border-t-0", CONSOLE.divider)}>
                  <IconChip icon={FiEye} tone="violet" size="sm" />
                  <div className="min-w-0">
                    <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>
                      {insights.data.blocked_join_attempts.total} refused join attempt{insights.data.blocked_join_attempts.total === 1 ? "" : "s"}
                    </p>
                    <p className={cx("text-[12px]", CONSOLE.faint)}>
                      {insights.data.blocked_join_attempts.reasons.slice(0, 2).map((x) => x.label).join(" · ")}
                    </p>
                  </div>
                </li>
              )}
              {!rows.some((r) => r.pct != null && r.pct >= 80) && !(insights.data?.blocked_join_attempts?.total > 0) && (
                <li className={cx("py-3 text-[13px]", CONSOLE.muted)}>No event is near capacity and no join was refused.</li>
              )}
            </ul>
          </ConsolePanel>

          <ConsolePanel title="Keep audiences distinct" subtitle="Apply the correct privacy boundary">
            <ul>
              {[
                [FiEye, "Playback sessions", "Engagement signals, not people records"],
                [FiUserCheck, "Registered & invited viewers", "Per event, under its access settings"],
                [FiUsers, "Your team", "Managed in Organization › Team & roles"],
              ].map(([Icon, title, detail]) => (
                <li key={title} className={cx("flex items-center gap-3 border-t py-3 first:border-t-0", CONSOLE.divider)}>
                  <IconChip icon={Icon} tone="violet" size="sm" />
                  <div>
                    <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{title}</p>
                    <p className={cx("text-[12px]", CONSOLE.faint)}>{detail}</p>
                  </div>
                </li>
              ))}
            </ul>
          </ConsolePanel>
        </div>
      </div>

      <Panel title="Attendance" eyebrow={`Last ${range}`}>
        <FactGrid
          columns={4}
          facts={[
            {
              // NOT "Unique attendees": the figure is claimed private-registration emails
              // plus the sum of each event's PEAK concurrency, so one person at two events
              // counts twice. The footnote below already said the method was estimated
              // while the label still claimed uniqueness — this is the same correction
              // analytics() made when "Total Viewers" became peak_viewers_summed.
              label: "Estimated attendees",
              value: attendance.loading ? null : a.unique_attendees ?? null,
              // Reasons are PRINTED now, not just hovered, so each one has to be true in the
              // state it actually appears in. While the panel is loading a figure is not
              // missing, it is not here yet — and "No attendance data" under every card would
              // be a false statement that resolves a second later. No reason while loading;
              // the dash alone is the honest placeholder.
              reason: attendance.loading ? undefined : "No attendance data for this window",
            },
            {
              label: "Returning attendees",
              value: attendance.loading ? null : a.returning ?? null,
              reason: attendance.loading ? undefined : "No attendance data for this window",
            },
            {
              label: "Average watch time",
              value:
                attendance.loading || a.avg_watch_minutes == null ? null : `${a.avg_watch_minutes}m`,
              // Distinct from a measured 0.0, which renders as "0m": this is the never-sampled
              // case, where services/org.py deliberately returns null rather than zero.
              reason: attendance.loading ? undefined : "No sampled viewer data for this window",
            },
            // Counted, not estimated: distinct registrations for registration-gated or
            // private events in the window whose registrant was admitted to the live stream.
            attendanceFact(insights),
          ]}
        />
        {/* Honest, not hidden: unique_attendees/avg_watch_minutes are derived from 15s
            concurrent-viewer sampling, not counted per person — see
            services/org.py::audience_attendance's docstring. */}
        {!attendance.loading && (a.unique_attendees_estimated || a.avg_watch_minutes_estimated) && (
          <p className={cx("mt-4 border-t pt-3 text-[12px] leading-relaxed", CONSOLE.divider, CONSOLE.faint)}>
            Attendees and watch time are estimated from sampled concurrent viewers, not counted
            per person. Registration → attendance counts registered viewers who joined the live stream.
          </p>
        )}
      </Panel>

      <Panel
        title="Audience insights"
        eyebrow={`Last ${range}`}
        description="Viewers who joined a live stream, counted once per event. Hosts and staff are not included."
      >
        {insights.error ? (
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
            <span className={CONSOLE.muted}>Couldn&apos;t load audience insights.</span>
            <ConsoleButton size="sm" variant="secondary" onClick={insights.reload}>Retry</ConsoleButton>
          </div>
        ) : insights.loading || !insights.data ? (
          <p className={cx("text-[13px]", CONSOLE.faint)}>Loading audience insights…</p>
        ) : (
          <AudienceInsights data={insights.data} />
        )}
      </Panel>

      <Panel title="Controls that shape the audience">
        {/* Two columns INSIDE the section — a grid where it helps reading, which is a
            different thing from the page-level rail this replaced. */}
        <ul className="grid gap-1 sm:grid-cols-2">
          {[
            [FiUsers, "Members & Access", "/organization/users", "Who inside the organization can operate events."],
            // Playback & Access is not listed: it was removed from the Organization rail, and
            // the gates it reads are SET on the event, which is where "Live Events" below
            // already points. Its route and page are untouched.
            [FiCalendar, "Live Events", "/organization/events", "Capacity, schedule and the watch window."],
            [FiTrendingUp, "Analytics", "/organization/analytics", "Engagement once they are in the room."],
          ].map(([Icon, label, to, desc]) => (
            <li key={to}>
              <Link
                to={to}
                className={cx(
                  "flex items-start gap-2.5 rounded-lg px-2 py-2 transition-colors duration-150 motion-reduce:transition-none",
                  "hover:bg-slate-100 dark:hover:bg-white/[0.06]"
                )}
              >
                <Icon className={cx("mt-0.5 shrink-0 text-[14px]", CONSOLE.faint)} aria-hidden="true" />
                <span className="min-w-0">
                  <span className={cx("block text-[13px] font-semibold", CONSOLE.heading)}>{label}</span>
                  <span className={cx("block text-[12px]", CONSOLE.faint)}>{desc}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
