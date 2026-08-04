// client/src/pages/organization/Analytics.jsx
// Analytics & Business Intelligence — real event performance for an organization.
// Route: /organization/analytics. Rendered inside OrganizationLayout.
//
// Every number here is measured. Where this platform has no source for something the brief asks
// for (viewer geography, device mix after the fact, traffic sources, bandwidth), the API returns a
// reason in `unavailable` and the "Not measured" panel prints it — rather than the page drawing a
// convincing pie chart of numbers nobody collected.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  FiCalendar, FiChevronDown, FiDownload, FiUsers, FiClock, FiTrendingUp, FiActivity,
  FiFilter, FiX, FiRefreshCw, FiRadio, FiPrinter, FiInfo, FiMic,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import Badge from "../../ui/Badge";
import StatsCard from "../../ui/StatsCard";
import { notify } from "../../ui/Toast";
import { AreaChart, BarChart, ChartCard, Funnel, HeatMap } from "../../ui/charts";
import DataTable from "../../components/admin/DataTable";
import useApi from "../../hooks/useApi";
import api from "../../api";
import { fmtDate } from "../../data/events";
import {
  CHART, PERIODS, EVENT_SORTS, DATASETS, WEEKDAYS,
  num, pct, dur, hours, growth, series, engagementTone, localZone,
} from "../../data/analytics";

const control =
  "rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 shadow-sm outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "engagement", label: "Engagement" },
  { key: "events", label: "Events" },
  { key: "people", label: "Speakers & attendees" },
  { key: "live", label: "Live" },
];

export default function OrganizationAnalytics() {
  const [period, setPeriod] = useState("monthly");
  const [range, setRange] = useState({ start: "", end: "" });
  const [filters, setFilters] = useState({});
  const [tab, setTab] = useState("overview");
  const [sort, setSort] = useState("attended");
  const [showFilters, setShowFilters] = useState(false);
  const tz = useMemo(() => localZone(), []);

  // One query string for every panel, so a filter change moves the whole page together instead of
  // leaving one chart on the old window.
  const qs = useMemo(() => {
    const search = new URLSearchParams({ period, tz });
    if (period === "custom") {
      if (range.start) search.set("start", new Date(range.start).toISOString());
      if (range.end) search.set("end", new Date(range.end).toISOString());
    }
    Object.entries(filters).forEach(([key, value]) => {
      if (value) search.set(key, value);
    });
    return search.toString();
  }, [period, range, filters, tz]);

  const overview = useApi(() => api.get(`/analytics/overview?${qs}`).then((r) => r.data));
  const trends = useApi(() => api.get(`/analytics/trends?${qs}`).then((r) => r.data));
  const options = useApi(() => api.get("/analytics/filters").then((r) => r.data));

  const reload = () => { overview.reload(); trends.reload(); };

  const o = overview.data;
  const activeFilters = Object.entries(filters).filter(([, v]) => v);

  const kpis = o ? [
    {
      title: "Attendees", value: o.audience.attended, icon: FiUsers, accent: "violet",
      ...growth(o.growth.attended),
    },
    {
      title: "Watch time", value: hours(o.audience.total_watch_seconds), icon: FiClock, accent: "blue",
      ...growth(o.growth.watch_seconds),
    },
    {
      title: "Peak concurrent", value: o.audience.peak_concurrent, icon: FiTrendingUp, accent: "emerald",
      ...growth(o.growth.peak_viewers),
    },
    {
      title: "Engagement", value: o.engagement.engagement_score, suffix: "%", icon: FiActivity,
      accent: "amber",
    },
  ] : [];

  const exportUrl = (dataset, format) =>
    `${api.defaults.baseURL}/analytics/export?${qs}&dataset=${dataset}&format=${format}`;

  // Export goes through axios, not a bare <a href>: the endpoint needs the bearer token, which an
  // anchor cannot carry. Same reason the speaker console fetches presentation PDFs as blobs.
  const download = async (dataset, format) => {
    try {
      const res = await api.get(exportUrl(dataset, format).replace(api.defaults.baseURL, ""), {
        responseType: format === "json" ? "json" : "blob",
      });
      const blob = format === "json"
        ? new Blob([JSON.stringify(res.data, null, 2)], { type: "application/json" })
        : res.data;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `zoikostream-${dataset}-${period}.${format}`;
      a.click();
      URL.revokeObjectURL(url);
      notify.success(`${dataset} exported as ${format.toUpperCase()}`);
    } catch {
      notify.error("That export could not be generated");
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Analytics</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {o ? `${o.window.label} · every figure measured from live event records` : "Loading…"}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative">
            <FiCalendar className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <select value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Report period"
              className={cx(control, "appearance-none pl-9 pr-8")}>
              {PERIODS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
            <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          </div>

          {period === "custom" && (
            <div className="flex items-center gap-1.5">
              <input type="date" value={range.start} aria-label="Range start"
                onChange={(e) => setRange({ ...range, start: e.target.value })} className={control} />
              <span className="text-slate-400">–</span>
              <input type="date" value={range.end} aria-label="Range end"
                onChange={(e) => setRange({ ...range, end: e.target.value })} className={control} />
            </div>
          )}

          <Button size="sm" variant="secondary" onClick={() => setShowFilters((s) => !s)}>
            <FiFilter className="text-base" /> Filters
            {activeFilters.length > 0 && (
              <span className="ml-1 rounded-full bg-emerald-500 px-1.5 text-[10px] font-semibold text-white">
                {activeFilters.length}
              </span>
            )}
          </Button>
          <Button size="sm" variant="secondary" onClick={reload} disabled={overview.loading}>
            <FiRefreshCw className={cx("text-base", overview.loading && "animate-spin")} />
          </Button>
          <ExportMenu onDownload={download} />
        </div>
      </div>

      {showFilters && (
        <Card padding="md">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <FilterSelect label="Host" value={filters.host_id} onChange={(v) => setFilters({ ...filters, host_id: v })}
              items={(options.data?.hosts || []).map((h) => ({ value: h.id, label: h.name }))} />
            <FilterSelect label="Speaker" value={filters.speaker_id} onChange={(v) => setFilters({ ...filters, speaker_id: v })}
              items={(options.data?.speakers || []).map((s) => ({ value: s.id, label: s.name }))} />
            <FilterSelect label="Department" value={filters.department} onChange={(v) => setFilters({ ...filters, department: v })}
              items={(options.data?.departments || []).map((d) => ({ value: d, label: d }))} />
            <FilterSelect label="Category" value={filters.category} onChange={(v) => setFilters({ ...filters, category: v })}
              items={(options.data?.categories || []).map((c) => ({ value: c, label: c }))} />
            <FilterSelect label="Location" value={filters.location} onChange={(v) => setFilters({ ...filters, location: v })}
              items={(options.data?.locations || []).map((l) => ({ value: l, label: l }))} />
            <FilterSelect label="Tag" value={filters.tag} onChange={(v) => setFilters({ ...filters, tag: v })}
              items={(options.data?.tags || []).map((t) => ({ value: t, label: t }))} />
            <FilterSelect label="Status" value={filters.event_status} onChange={(v) => setFilters({ ...filters, event_status: v })}
              items={(options.data?.statuses || []).map((s) => ({ value: s, label: s }))} />
            <div className="flex items-end">
              {activeFilters.length > 0 && (
                <button onClick={() => setFilters({})}
                  className="inline-flex items-center gap-1 rounded-lg px-2 py-2 text-sm font-medium text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800">
                  <FiX /> Clear all
                </button>
              )}
            </div>
          </div>
          <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
            Timezone: <strong className="font-medium">{tz}</strong> — trends and the heat map are
            bucketed in your local time, not UTC.
          </p>
        </Card>
      )}

      {overview.error ? (
        <Card className="py-12 text-center text-sm text-rose-600 dark:text-rose-400">
          Analytics could not be loaded. <button onClick={reload} className="underline">Try again</button>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {overview.loading || !o
              ? Array.from({ length: 4 }, (_, i) => (
                <Card key={i} className="h-28"><div className="zk-skeleton h-full rounded bg-slate-100 dark:bg-slate-800" /></Card>
              ))
              : kpis.map((k) => <StatsCard key={k.title} {...k} />)}
          </div>

          {/* Event lifecycle strip — real counts, and where they came from is obvious. */}
          {o && (
            <Card padding="md">
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
                {[
                  ["Total events", o.events.total], ["Completed", o.events.completed],
                  ["Live now", o.events.live], ["Upcoming", o.events.upcoming],
                  ["Cancelled", o.events.cancelled], ["Registrations", o.audience.registrations],
                ].map(([label, value]) => (
                  <div key={label}>
                    <p className="text-xs text-slate-500 dark:text-slate-400">{label}</p>
                    <p className="text-xl font-bold tabular-nums text-slate-900 dark:text-white">{num(value)}</p>
                  </div>
                ))}
              </div>
              <div className="mt-4 grid grid-cols-2 gap-4 border-t border-slate-100 pt-4 sm:grid-cols-4 dark:border-slate-800">
                {[
                  ["Attendance rate", pct(o.audience.attendance_rate, 1)],
                  ["Avg. watch time", dur(o.audience.avg_watch_seconds)],
                  ["Avg. session length", dur(o.live.avg_broadcast_seconds)],
                  ["Sessions per attendee", num(o.audience.avg_sessions_per_attendee)],
                ].map(([label, value]) => (
                  <div key={label}>
                    <p className="text-xs text-slate-500 dark:text-slate-400">{label}</p>
                    <p className="font-semibold tabular-nums text-slate-800 dark:text-slate-100">{value}</p>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* Tabs */}
          <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-200 pb-px dark:border-slate-800">
            {TABS.map((t) => (
              <button key={t.key} onClick={() => setTab(t.key)}
                className={cx(
                  "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition",
                  tab === t.key
                    ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                    : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
                )}>
                {t.label}
              </button>
            ))}
          </div>

          {tab === "overview" && <OverviewTab qs={qs} trends={trends} overview={overview} tz={tz} />}
          {tab === "engagement" && <EngagementTab qs={qs} />}
          {tab === "events" && <EventsTab qs={qs} sort={sort} setSort={setSort} onExport={download} />}
          {tab === "people" && <PeopleTab qs={qs} onExport={download} />}
          {tab === "live" && <LiveTab />}
        </>
      )}
    </div>
  );
}

// ── tabs ─────────────────────────────────────────────────────────────────────

function OverviewTab({ qs, trends, overview, tz }) {
  const heatmap = useApi(() => api.get(`/analytics/heatmap?${qs}`).then((r) => r.data));
  const bucket = trends.data?.window?.bucket || "day";
  const rows = trends.data?.series || [];
  const o = overview.data;

  return (
    <>
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <AreaChart
          title="Audience" subtitle={`Attendance and peak concurrent · ${trends.data?.window?.label || ""}`}
          data={series(rows, ["attended", "peak_viewers"], bucket)}
          keys={[
            { key: "attended", name: "Attended", color: CHART.emerald },
            { key: "peak_viewers", name: "Peak concurrent", color: CHART.violet },
          ]}
          type="line" loading={trends.loading} empty={!rows.length}
        />
        <AreaChart
          title="Registrations vs. attendance" subtitle="Did the people who signed up turn up?"
          data={series(rows, ["registrations", "attended"], bucket)}
          keys={[
            { key: "registrations", name: "Registered", color: CHART.blue },
            { key: "attended", name: "Attended", color: CHART.emerald },
          ]}
          type="area" loading={trends.loading} empty={!rows.length}
        />
        <BarChart
          title="Watch time" subtitle="Total seconds watched per period"
          data={series(rows, ["watch_seconds"], bucket).map((p) => ({ label: p.label, value: p.watch_seconds }))}
          color={CHART.violet} loading={trends.loading} empty={!rows.length}
        />
        <BarChart
          title="Events run" subtitle="Completed and cancelled"
          data={series(rows, ["events"], bucket).map((p) => ({ label: p.label, value: p.events }))}
          color={CHART.blue} loading={trends.loading} empty={!rows.length}
        />
      </div>

      <div className="mt-6">
        <HeatMap
          title="When people watch" subtitle={`Average concurrent viewers by weekday and hour · ${tz}`}
          cells={heatmap.data?.cells || []} weekdays={heatmap.data?.weekdays || WEEKDAYS}
          color={CHART.violet} loading={heatmap.loading}
        />
      </div>

      {o?.unavailable && <NotMeasured items={o.unavailable} />}
    </>
  );
}

function EngagementTab({ qs }) {
  const engagement = useApi(() => api.get(`/analytics/engagement?${qs}`).then((r) => r.data));
  const e = engagement.data;

  if (engagement.loading) {
    return <Card className="h-64"><div className="zk-skeleton h-full rounded bg-slate-100 dark:bg-slate-800" /></Card>;
  }
  if (!e) return null;

  const tiles = [
    ["Chat messages", e.messages], ["Questions asked", e.questions],
    ["Questions answered", e.questions_answered], ["Poll votes", e.poll_votes],
    ["Polls run", e.polls], ["Reactions", e.reactions],
    ["Peak hands raised", e.hands], ["Announcements", e.announcements],
  ];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {tiles.map(([label, value]) => (
          <Card key={label} padding="md">
            <p className="text-xs text-slate-500 dark:text-slate-400">{label}</p>
            <p className="mt-1 text-2xl font-bold tabular-nums text-slate-900 dark:text-white">{num(value)}</p>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card padding="md">
          <h2 className="font-semibold text-slate-900 dark:text-white">Participation</h2>
          <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">
            Distinct people who interacted, not just watched
          </p>
          <dl className="space-y-3">
            {[
              ["People who interacted", num(e.participants)],
              ["…in chat", num(e.chat_participants)],
              ["…in Q&A", num(e.qa_participants)],
              ["Interactions per person", num(e.avg_interactions_per_attendee)],
              ["Answer rate", pct(e.answer_rate, 1)],
            ].map(([label, value]) => (
              <div key={label} className="flex items-baseline justify-between gap-3 border-b border-slate-50 pb-2 last:border-0 dark:border-slate-800/60">
                <dt className="text-sm text-slate-600 dark:text-slate-300">{label}</dt>
                <dd className="font-semibold tabular-nums text-slate-800 dark:text-slate-100">{value}</dd>
              </div>
            ))}
          </dl>
        </Card>

        <Card padding="md">
          <h2 className="font-semibold text-slate-900 dark:text-white">Engagement score</h2>
          <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">Weighted interactions per viewer</p>
          <div className="mb-3 flex items-end gap-3">
            <span className="text-4xl font-bold tabular-nums text-slate-900 dark:text-white">
              {e.engagement_score}
            </span>
            <span className="pb-1.5 text-sm text-slate-400">/ 100</span>
          </div>
          <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div className="h-full rounded-full transition-all"
              style={{ width: `${e.engagement_score}%`, background: engagementTone(e.engagement_score) }} />
          </div>
          {/* The formula, printed. A composite score nobody can reproduce is not a measurement. */}
          <p className="mt-4 flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            <FiInfo className="mt-0.5 shrink-0" />
            <span>{e.score_formula}</span>
          </p>
        </Card>
      </div>
    </div>
  );
}

function EventsTab({ qs, sort, setSort, onExport }) {
  const events = useApi(() => api.get(`/analytics/events?${qs}&sort=${sort}`).then((r) => r.data));
  const rows = events.data?.events || [];
  const [openId, setOpenId] = useState(null);

  const columns = [
    {
      key: "title", header: "Event", className: "whitespace-nowrap",
      render: (r) => (
        <button onClick={() => setOpenId(openId === r.id ? null : r.id)}
          className="text-left font-medium text-slate-800 hover:text-emerald-600 dark:text-slate-100">
          {r.title}
        </button>
      ),
    },
    { key: "start_time", header: "Date", className: "whitespace-nowrap", render: (r) => (r.start_time ? fmtDate(r.start_time) : "—") },
    { key: "registrations", header: "Registered", align: "right", render: (r) => num(r.registrations) },
    { key: "attended", header: "Attended", align: "right", render: (r) => num(r.attended) },
    { key: "attendance_rate", header: "Rate", align: "right", render: (r) => pct(r.attendance_rate, 0) },
    { key: "peak_viewers", header: "Peak", align: "right", render: (r) => num(r.peak_viewers) },
    { key: "watch_seconds", header: "Watch time", align: "right", render: (r) => dur(r.watch_seconds) },
    { key: "replay_views", header: "Replays", align: "right", render: (r) => num(r.replay_views) },
    {
      key: "engagement_score", header: "Engagement", className: "whitespace-nowrap",
      render: (r) => (
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div className="h-full rounded-full"
              style={{ width: `${r.engagement_score}%`, background: engagementTone(r.engagement_score) }} />
          </div>
          <span className="tabular-nums font-medium text-slate-700 dark:text-slate-200">{r.engagement_score}%</span>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="relative">
          <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort events"
            className={cx(control, "appearance-none pr-8")}>
            {EVENT_SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
        </div>
        <Button size="sm" variant="secondary" onClick={() => onExport("events", "xlsx")}>
          <FiDownload className="text-base" /> Export events
        </Button>
      </div>

      <Card padding="none" className="overflow-hidden">
        {events.loading ? (
          <div className="zk-skeleton m-4 h-56 rounded bg-slate-100 dark:bg-slate-800" />
        ) : rows.length === 0 ? (
          <p className="py-14 text-center text-sm text-slate-400">No events in this window.</p>
        ) : (
          <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} minWidth={980} />
        )}
      </Card>

      {openId && <EventDeepDive eventId={openId} onClose={() => setOpenId(null)} />}
    </div>
  );
}

function EventDeepDive({ eventId, onClose }) {
  const detail = useApi(() => api.get(`/analytics/events/${eventId}`).then((r) => r.data));
  const d = detail.data;

  if (detail.loading) {
    return <Card className="h-64"><div className="zk-skeleton h-full rounded bg-slate-100 dark:bg-slate-800" /></Card>;
  }
  if (!d) return null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-semibold text-slate-900 dark:text-white">
          {d.event.title}
          <span className="ml-2 text-sm font-normal text-slate-500">
            {d.sample_count} samples · one every 15s
          </span>
        </h2>
        <div className="flex gap-2">
          <Link to={`/organization/events/${eventId}`}
            className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
            Open event
          </Link>
          <button onClick={onClose} aria-label="Close"
            className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
            Close
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Funnel title="Attendance funnel" subtitle="Registered → joined → stayed → interacted"
          stages={d.funnel} color={CHART.violet} />
        {d.retention.length > 0 ? (
          <AreaChart
            title="Audience retention" subtitle="Percentage of the peak audience still watching"
            data={d.retention.map((r) => ({ label: `${r.progress}%`, value: r.percent }))}
            keys={[{ key: "value", name: "Still watching", color: CHART.rose }]}
            type="area" suffix="%"
          />
        ) : (
          <ChartCard title="Audience retention"
            subtitle="Percentage of the peak audience still watching" empty
            emptyText="Too few samples to draw a retention curve" />
        )}
      </div>

      {d.timeline.length > 0 && (
        <AreaChart
          title="Engagement timeline" subtitle="Interactions over the course of the event"
          data={d.timeline.map((t) => ({
            label: t.t ? new Date(t.t).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "",
            viewers: t.viewers, messages: t.messages, reactions: t.reactions,
          }))}
          keys={[
            { key: "viewers", name: "Viewers", color: CHART.violet },
            { key: "messages", name: "Messages", color: CHART.blue },
            { key: "reactions", name: "Reactions", color: CHART.amber },
          ]}
          type="line"
        />
      )}
    </div>
  );
}

function PeopleTab({ qs, onExport }) {
  const speakers = useApi(() => api.get(`/analytics/speakers?${qs}`).then((r) => r.data));
  const attendees = useApi(() => api.get(`/analytics/attendees?${qs}`).then((r) => r.data));

  const speakerColumns = [
    { key: "name", header: "Speaker", render: (r) => <span className="font-medium text-slate-800 dark:text-slate-100">{r.name || "—"}</span> },
    { key: "department", header: "Department", render: (r) => r.department || "—" },
    { key: "events", header: "Events", align: "right", render: (r) => num(r.events) },
    {
      key: "speaking_seconds", header: "Speaking time", align: "right",
      // Null means the event never went live, which is different from "spoke for 0 seconds".
      render: (r) => (r.speaking_seconds === null
        ? <span className="text-slate-400" title="No completed broadcast to measure">—</span>
        : dur(r.speaking_seconds)),
    },
    { key: "questions_answered", header: "Answered", align: "right", render: (r) => num(r.questions_answered) },
    { key: "answer_rate", header: "Answer rate", align: "right", render: (r) => pct(r.answer_rate, 0) },
    { key: "avg_attendance", header: "Avg. audience", align: "right", render: (r) => num(r.avg_attendance) },
    { key: "drop_off_percent", header: "Drop-off", align: "right", render: (r) => pct(r.drop_off_percent, 1) },
  ];

  const attendeeColumns = [
    { key: "name", header: "Attendee", render: (r) => <span className="font-medium text-slate-800 dark:text-slate-100">{r.name || r.email || "—"}</span> },
    { key: "department", header: "Department", render: (r) => r.department || "—" },
    { key: "attended", header: "Attended", align: "right", render: (r) => num(r.attended) },
    { key: "attendance_rate", header: "Rate", align: "right", render: (r) => pct(r.attendance_rate, 0) },
    { key: "watch_seconds", header: "Watch time", align: "right", render: (r) => dur(r.watch_seconds) },
    { key: "sessions_joined", header: "Sessions", align: "right", render: (r) => num(r.sessions_joined) },
    { key: "questions_asked", header: "Questions", align: "right", render: (r) => num(r.questions_asked) },
    { key: "messages_sent", header: "Messages", align: "right", render: (r) => num(r.messages_sent) },
  ];

  return (
    <div className="space-y-6">
      <Card padding="none" className="overflow-hidden">
        <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <div>
            <h2 className="flex items-center gap-2 font-semibold text-slate-900 dark:text-white">
              <FiMic className="text-base" /> Speakers
            </h2>
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Speaking time is recorded when a broadcast ends
            </p>
          </div>
          <Button size="sm" variant="secondary" onClick={() => onExport("speakers", "csv")}>
            <FiDownload /> CSV
          </Button>
        </div>
        {speakers.loading ? <div className="zk-skeleton m-4 h-40 rounded bg-slate-100 dark:bg-slate-800" />
          : (speakers.data?.speakers?.length
            ? <DataTable columns={speakerColumns} rows={speakers.data.speakers} rowKey={(r) => r.id} minWidth={900} />
            : <p className="py-12 text-center text-sm text-slate-400">No speakers in this window.</p>)}
      </Card>

      {speakers.data?.unavailable && <NotMeasured items={speakers.data.unavailable} compact />}

      <Card padding="none" className="overflow-hidden">
        <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <div>
            <h2 className="flex items-center gap-2 font-semibold text-slate-900 dark:text-white">
              <FiUsers className="text-base" /> Attendees
            </h2>
            <p className="text-sm text-slate-500 dark:text-slate-400">Ranked by watch time</p>
          </div>
          <Button size="sm" variant="secondary" onClick={() => onExport("attendees", "csv")}>
            <FiDownload /> CSV
          </Button>
        </div>
        {attendees.loading ? <div className="zk-skeleton m-4 h-40 rounded bg-slate-100 dark:bg-slate-800" />
          : (attendees.data?.attendees?.length
            ? <DataTable columns={attendeeColumns} rows={attendees.data.attendees} rowKey={(r) => r.id} minWidth={900} />
            : <p className="py-12 text-center text-sm text-slate-400">No attendee records in this window.</p>)}
      </Card>

      {attendees.data?.unavailable && <NotMeasured items={attendees.data.unavailable} compact />}
    </div>
  );
}

function LiveTab() {
  // Polls, because this is the real-time screen and the server never caches it. 15s matches the
  // sampler's cadence — asking more often cannot produce a newer number.
  const live = useApi(() => api.get("/analytics/live").then((r) => r.data));
  const d = live.data;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Figures come from the analytics sampler, so they are up to {d?.sample_seconds ?? 15} seconds old.
        </p>
        <Button size="sm" variant="secondary" onClick={live.reload} disabled={live.loading}>
          <FiRefreshCw className={cx("text-base", live.loading && "animate-spin")} /> Refresh
        </Button>
      </div>

      {live.loading ? (
        <Card className="h-40"><div className="zk-skeleton h-full rounded bg-slate-100 dark:bg-slate-800" /></Card>
      ) : !d?.events?.length ? (
        <Card className="py-14 text-center">
          <FiRadio className="mx-auto mb-2 text-2xl text-slate-300 dark:text-slate-600" />
          <p className="font-medium text-slate-700 dark:text-slate-200">Nothing is on air</p>
          <p className="text-sm text-slate-500 dark:text-slate-400">Live figures appear here while an event is broadcasting.</p>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
            {[
              ["On air", d.totals.live_events], ["Viewers", d.totals.viewers],
              ["Participants", d.totals.participants], ["In the lobby", d.totals.waiting],
              ["Hands up", d.totals.hands],
            ].map(([label, value]) => (
              <Card key={label} padding="md">
                <p className="text-xs text-slate-500 dark:text-slate-400">{label}</p>
                <p className="mt-1 text-2xl font-bold tabular-nums text-slate-900 dark:text-white">{num(value)}</p>
              </Card>
            ))}
          </div>

          <div className="space-y-3">
            {d.events.map((ev) => (
              <Card key={ev.id} padding="md">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Badge tone={ev.status === "live" ? "danger" : "warning"} size="sm" dot>
                        {ev.status === "live" ? "LIVE" : "PAUSED"}
                      </Badge>
                      <Link to={`/organization/events/${ev.id}`}
                        className="truncate font-semibold text-slate-800 hover:text-emerald-600 dark:text-slate-100">
                        {ev.title}
                      </Link>
                    </div>
                    {!ev.has_sample && (
                      <p className="mt-0.5 text-xs text-slate-400">
                        Just went live — no sample yet.
                      </p>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
                    {[
                      ["Viewers", ev.viewers], ["Peak", ev.peak_viewers],
                      ["On stage", ev.on_stage], ["Lobby", ev.waiting], ["Hands", ev.hands],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <p className="text-[11px] text-slate-500 dark:text-slate-400">{label}</p>
                        <p className="font-semibold tabular-nums text-slate-800 dark:text-slate-100">{num(value)}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </>
      )}

      {d?.unavailable && <NotMeasured items={d.unavailable} />}
    </div>
  );
}

// ── shared bits ──────────────────────────────────────────────────────────────

/**
 * The honest panel. Everything the brief asks for that this platform has no source for, with the
 * reason the API gave. Rendering this instead of a plausible chart is the point.
 */
function NotMeasured({ items, compact = false }) {
  const entries = Object.entries(items || {});
  if (!entries.length) return null;
  return (
    <Card padding="md" className={compact ? "bg-slate-50/60 dark:bg-slate-800/30" : ""}>
      <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-800 dark:text-slate-100">
        <FiInfo className="text-base text-slate-400" /> Not measured
      </h2>
      <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
        These would need a source this deployment does not have. They are listed rather than estimated.
      </p>
      <dl className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
        {entries.map(([key, reason]) => (
          <div key={key} className="rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-800/60">
            <dt className="text-xs font-semibold capitalize text-slate-700 dark:text-slate-200">
              {key.replace(/_/g, " ")}
            </dt>
            <dd className="text-xs text-slate-500 dark:text-slate-400">{reason}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function ExportMenu({ onDownload }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <Button size="sm" onClick={() => setOpen((o) => !o)}>
        <FiDownload className="text-base" /> Export
      </Button>
      {open && (
        <>
          {/* Click-away scrim. A button, not a div: it is the same dismiss affordance either
              way, and as a button it is keyboard reachable instead of mouse-only. */}
          <button type="button" aria-label="Close the export menu"
            className="fixed inset-0 z-10 cursor-default" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-1.5 w-60 overflow-hidden rounded-xl border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-900">
            {DATASETS.map((d) => (
              <div key={d.key} className="flex items-center justify-between gap-1 px-2 py-1.5">
                <span className="text-sm text-slate-700 dark:text-slate-200">{d.label}</span>
                <span className="flex gap-1">
                  {["csv", "xlsx", "json"].map((fmt) => (
                    <button key={fmt}
                      onClick={() => { onDownload(d.key, fmt); setOpen(false); }}
                      className="rounded border border-slate-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800">
                      {fmt}
                    </button>
                  ))}
                </span>
              </div>
            ))}
            <div className="mt-1 border-t border-slate-100 px-2 pb-1 pt-2 dark:border-slate-800">
              <button onClick={() => { window.print(); setOpen(false); }}
                className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-600 hover:text-slate-900 dark:text-slate-300">
                <FiPrinter /> Print / Save as PDF
              </button>
              {/* Honest about where PDF comes from — the browser lays out the charts already on
                  screen, which beats a server-side PDF toolchain this stack does not ship. */}
              <p className="mt-1 text-[10px] leading-snug text-slate-400">
                PDF uses your browser&rsquo;s print engine, so the charts render exactly as shown.
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function FilterSelect({ label, value, onChange, items }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">{label}</span>
      <div className="relative">
        <select value={value || ""} onChange={(e) => onChange(e.target.value)}
          className={cx(control, "w-full appearance-none pr-8")}
          disabled={!items.length}>
          <option value="">{items.length ? `All ${label.toLowerCase()}s` : "None recorded"}</option>
          {items.map((i) => <option key={i.value} value={i.value}>{i.label}</option>)}
        </select>
        <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
      </div>
    </label>
  );
}
