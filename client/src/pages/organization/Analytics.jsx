// client/src/pages/organization/Analytics.jsx
// Analytics Dashboard — event performance for an organization.
// Route: /organization/analytics. Rendered inside OrganizationLayout.
// Backed by GET /organization/analytics (services/org.py analytics()) — real numbers from
// Event + BroadcastSession + AnalyticsSnapshot. Device/location/traffic-source breakdowns
// aren't shown here: nothing in this stack persists them historically (see the endpoint's
// own `breakdowns_note`), so a bar chart for them would be invented, not measured.
import { useMemo, useState } from "react";
import { FiCalendar, FiChevronDown, FiDownload, FiUsers, FiClock, FiTrendingUp, FiActivity, FiInfo } from "react-icons/fi";
import { CONSOLE, cx } from "../../ui/tokens";
import api from "../../api";
import useApi from "../../hooks/useApi";
import Card from "../../ui/Card";
import Button, { ConsoleButton } from "../../ui/Button";
import StatsCard from "../../ui/StatsCard";
import Spinner from "../../ui/Spinner";
import { notify } from "../../ui/Toast";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  XAxis,
  YAxis,
} from "recharts";
import { ChartCard, ChartTooltip, PieChart } from "../../ui/charts";
import DataTable from "../../components/admin/DataTable";
import { fmtDate } from "../../data/events";

const CHART = { violet: "#7c3aed", blue: "#3b82f6", emerald: "#10b981", amber: "#f59e0b", rose: "#f43f5e" };

const RANGES = [
  { key: "7d", label: "Last 7 days" },
  { key: "30d", label: "Last 30 days" },
  { key: "90d", label: "Last 90 days" },
  { key: "12m", label: "Last 12 months" },
];

const engTone = (v) =>
  v >= 70 ? CHART.emerald : v >= 55 ? CHART.amber : CHART.rose;

// The ranking metric switcher. Each entry knows its own label and number format so the
// panel header and bar readouts stay correct without a second lookup table.
const METRICS = [
  { value: "viewers", label: "Viewers", rankLabel: "peak viewers", format: (v) => v.toLocaleString() },
  { value: "watch_hours", label: "Watch time", rankLabel: "hours watched", format: (v) => `${v.toLocaleString()} hrs` },
  { value: "engagement", label: "Engagement", rankLabel: "engagement score", format: (v) => `${v}%` },
];

// DataTable already ships sorting, search and pagination; this table opted into none of them,
// so a 30-event window was a fixed wall of rows. Every column is sortable now, with the
// numeric ones sorting on their value rather than their formatted string.
const reportColumns = [
  { key: "event", header: "Event", sortable: true, className: "whitespace-nowrap", render: (r) => <span className="font-medium text-slate-800 dark:text-slate-100">{r.event}</span> },
  { key: "date", header: "Date", sortable: true, sortValue: (r) => (r.date ? new Date(r.date).getTime() : 0), className: "whitespace-nowrap", render: (r) => (r.date ? fmtDate(r.date) : "—") },
  { key: "viewers", header: "Peak Viewers", align: "right", sortable: true, className: "whitespace-nowrap", render: (r) => r.viewers.toLocaleString() },
  { key: "watch_hours", header: "Watch Time", align: "right", sortable: true, className: "whitespace-nowrap", render: (r) => `${r.watch_hours.toLocaleString()} hrs` },
  { key: "engagement", header: "Engagement", sortable: true, className: "whitespace-nowrap", render: (r) => (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div className="h-full rounded-full" style={{ width: `${r.engagement}%`, background: engTone(r.engagement) }} />
      </div>
      <span className="tabular-nums font-medium text-slate-700 dark:text-slate-200">{r.engagement}%</span>
    </div>
  ) },
];

// Segmented control — the page's one interaction primitive, used for the chart type and the
// ranking metric. Both only re-read data already on the client, so switching is instant.
function Segmented({ value, onChange, options }) {
  return (
    <div
      role="group"
      className={cx("inline-flex rounded-lg p-0.5", CONSOLE.segment)}
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          aria-pressed={value === o.value}
          className={cx(
            "rounded-[6px] px-2.5 py-1 text-xs font-semibold transition-colors duration-150 motion-reduce:transition-none",
            value === o.value ? CONSOLE.segmentOn : CONSOLE.segmentOff
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function RankedBars({ title, subtitle, items, color, format, action }) {
  const max = Math.max(...items.map((i) => i.value)) || 1;
  return (
    <Card padding="md">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold text-slate-900 dark:text-white">{title}</h2>
          {subtitle && <p className="text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
        </div>
        {action}
      </div>
      {items.length === 0 ? (
        <p className="py-6 text-center text-sm text-slate-400 dark:text-slate-500">No events with viewers in this window yet.</p>
      ) : (
        <ul className="space-y-3.5">
          {items.map((i) => (
            <li key={i.label} className="group -mx-2 rounded-lg px-2 py-1 transition-colors hover:bg-slate-50 motion-reduce:transition-none dark:hover:bg-white/[0.03]">
              <div className="mb-1 flex items-center justify-between gap-2 text-sm">
                <span className="truncate pr-2 text-slate-700 dark:text-slate-200">{i.label}</span>
                <span className="shrink-0 font-medium tabular-nums text-slate-500 dark:text-slate-400">{format(i.value)}</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                {/* Width transitions, so switching the ranking metric animates rather than
                    snapping to a different set of lengths. */}
                <div
                  className="h-full rounded-full transition-[width] duration-500 ease-out motion-reduce:transition-none"
                  style={{ width: `${(i.value / max) * 100}%`, background: color }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function OrganizationAnalytics() {
  const [range, setRange] = useState("30d");
  const { data, loading, error, reload } = useApi(() =>
    api.get("/organization/analytics", { params: { range } }).then((r) => r.data)
  );

  // useApi fetches on mount and on reload() only — it has no deps array. Without this the
  // range <select> changed the SUBTITLES and nothing else: the request was never re-sent, so
  // "Last 7 days" showed 30-day figures. Same guarded-render pattern the Overview uses.
  const [lastRange, setLastRange] = useState(range);
  if (range !== lastRange) {
    setLastRange(range);
    reload();
  }

  const rangeLabel = RANGES.find((r) => r.key === range)?.label ?? "";

  // Which metric drives the trend chart and the ranking list. Both read the payload that is
  // already on the client — switching them costs no request.
  const [metric, setMetric] = useState("viewers");
  const [chartType, setChartType] = useState("area");

  const kpis = useMemo(() => {
    const s = data?.summary || { viewers: 0, watch_hours: 0, peak: 0, engagement: 0 };
    return [
      { title: "Total Viewers", value: s.viewers, icon: FiUsers, accent: "violet" },
      { title: "Watch Time", value: s.watch_hours, suffix: " hrs", decimals: 1, icon: FiClock, accent: "blue" },
      { title: "Peak Concurrent Viewers", value: s.peak, icon: FiTrendingUp, accent: "emerald" },
      { title: "Avg. Engagement", value: s.engagement, suffix: "%", icon: FiActivity, accent: "amber" },
    ];
  }, [data]);

  // Memoised for identity, not cost: `|| []` minted a fresh array every render, which made
  // every derivation below recompute on any state change (metric switch, chart toggle).
  const reports = useMemo(() => data?.reports || [], [data]);

  // ── Everything below is DERIVED from the payload already fetched — reports[] carries
  // per-event viewers, watch_hours, engagement and date, and only the table was reading it.
  // No new endpoint, no invented figures.
  const trend = useMemo(() => {
    const viewership = data?.trends?.viewership || [];
    const watch = data?.trends?.watch_time || [];
    // Merge the two series on their shared bucket label so one canvas can carry both.
    return viewership.map((v, i) => ({
      label: v.label,
      viewers: v.value,
      watch_hours: watch[i]?.value ?? 0,
    }));
  }, [data]);

  // Ranking is re-sorted client-side, so the metric switcher is instant. The server's
  // top_events is peak-only and capped at 5; reports[] has every event.
  const ranked = useMemo(() => {
    const key = metric === "watch_hours" ? "watch_hours" : metric === "engagement" ? "engagement" : "viewers";
    return [...reports]
      .filter((r) => r[key] > 0)
      .sort((a, b) => b[key] - a[key])
      .slice(0, 8)
      .map((r) => ({ label: r.event, value: r[key] }));
  }, [reports, metric]);

  // Engagement mix: how many events land in each band. engagement_score() is a real measure
  // (messages/questions/reactions against peak viewers), so these buckets are measured.
  const engagementMix = useMemo(() => {
    const scored = reports.filter((r) => r.viewers > 0);
    const band = (v) => (v >= 70 ? "Strong (70%+)" : v >= 55 ? "Moderate (55–69%)" : "Low (<55%)");
    const counts = { "Strong (70%+)": 0, "Moderate (55–69%)": 0, "Low (<55%)": 0 };
    scored.forEach((r) => { counts[band(r.engagement)] += 1; });
    return Object.entries(counts)
      .filter(([, v]) => v > 0)
      .map(([label, value]) => ({ label, value }));
  }, [reports]);

  // Watch hours against peak viewers, one point per event: the shape tells you whether big
  // audiences actually stayed. Two events with equal peaks can sit far apart vertically.
  const retention = useMemo(
    () =>
      reports
        .filter((r) => r.viewers > 0)
        .map((r) => ({ x: r.viewers, y: r.watch_hours, z: r.engagement, name: r.event })),
    [reports]
  );

  const exportReport = () => {
    const head = ["Event", "Date", "Peak Viewers", "Watch Time (hrs)", "Engagement (%)"];
    const body = reports.map((r) => [r.event, r.date, r.viewers, r.watch_hours, r.engagement]);
    const csv = [head, ...body]
      .map((row) => row.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(","))
      .join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `zoikostream-analytics-${range}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    notify.success("Report exported");
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Analytics</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Understand how your live events are performing</p>
        </div>

        {/* Console vocabulary, not a page-local field style: CONSOLE.select and the console
            Button are what every other screen in this shell uses, so these two now carry the
            brand ramp and the shared focus ring instead of their own emerald copy. */}
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative">
            <FiCalendar className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 dark:text-neutral-400" aria-hidden="true" />
            <select
              value={range}
              onChange={(e) => setRange(e.target.value)}
              aria-label="Date range"
              className={cx(CONSOLE.select, "appearance-none pl-9 pr-8")}
            >
              {RANGES.map((r) => (
                <option key={r.key} value={r.key}>{r.label}</option>
              ))}
            </select>
            <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 dark:text-neutral-400" aria-hidden="true" />
          </div>
          <ConsoleButton onClick={exportReport} disabled={!reports.length} leftIcon={FiDownload}>
            Export Report
          </ConsoleButton>
        </div>
      </div>

      {/* A failed range switch shows a banner over the figures we already have rather than
          replacing them: losing the whole view because one refetch failed is worse than
          reading slightly stale numbers that say so. */}
      {error && data && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-5 py-3 text-sm text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-300">
          <span>Couldn't refresh — showing the last figures that loaded.</span>
          <Button size="sm" variant="secondary" onClick={reload}>Retry</Button>
        </div>
      )}

      {error && !data ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          Couldn't load analytics. Try refreshing the page.
        </div>
      ) : loading && !data ? (
        <div className="grid place-items-center py-20"><Spinner /></div>
      ) : (
        // Refetching dims the panels instead of unmounting them, so changing the range keeps
        // the layout still rather than flashing a spinner.
        <div
          className={cx(
            "space-y-6 transition-opacity duration-200 motion-reduce:transition-none",
            loading && "pointer-events-none opacity-50"
          )}
          aria-busy={loading || undefined}
        >
          {/* Summary cards */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {kpis.map((k) => (
              <StatsCard key={k.title} {...k} />
            ))}
          </div>

          {/* Trend — both series on one canvas with independent axes, so 24 viewers and
              4 watch-hours stay legible together instead of one flattening the other. */}
          <ChartCard
            title="Viewership & watch time"
            subtitle={`Peak viewers and hours watched · ${rangeLabel}`}
            height={300}
            empty={trend.length === 0}
            emptyText="No events in this window yet."
            action={
              <div className="flex items-center gap-2">
                <Segmented
                  value={chartType}
                  onChange={setChartType}
                  options={[
                    { value: "area", label: "Area" },
                    { value: "bar", label: "Bars" },
                  ]}
                />
              </div>
            }
          >
            <div style={{ height: 300 }}>
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={trend} margin={{ left: -12, right: -12, top: 8 }}>
                  <defs>
                    <linearGradient id="zk-an-viewers" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={CHART.violet} stopOpacity={0.35} />
                      <stop offset="100%" stopColor={CHART.violet} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-slate-100 dark:text-slate-800" vertical={false} />
                  <XAxis dataKey="label" fontSize={11} tickLine={false} axisLine={false} stroke="currentColor" className="text-slate-400 dark:text-slate-500" />
                  <YAxis yAxisId="v" fontSize={11} tickLine={false} axisLine={false} stroke={CHART.violet} width={40} />
                  <YAxis yAxisId="w" orientation="right" fontSize={11} tickLine={false} axisLine={false} stroke={CHART.blue} width={44} unit="h" />
                  <ChartTooltip
                    formatter={(v, name) => [name === "Watch hours" ? `${v} hrs` : v.toLocaleString(), name]}
                  />
                  <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
                  {chartType === "area" ? (
                    <Area
                      yAxisId="v"
                      type="monotone"
                      dataKey="viewers"
                      name="Peak viewers"
                      stroke={CHART.violet}
                      strokeWidth={2}
                      fill="url(#zk-an-viewers)"
                      activeDot={{ r: 5 }}
                    />
                  ) : (
                    <Bar yAxisId="v" dataKey="viewers" name="Peak viewers" fill={CHART.violet} radius={[6, 6, 0, 0]} maxBarSize={26} />
                  )}
                  <Line
                    yAxisId="w"
                    type="monotone"
                    dataKey="watch_hours"
                    name="Watch hours"
                    stroke={CHART.blue}
                    strokeWidth={2}
                    dot={{ r: 3 }}
                    activeDot={{ r: 5 }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>

          {/* Ranking + engagement mix */}
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <RankedBars
              title="Top Performing Events"
              subtitle={`Ranked by ${METRICS.find((m) => m.value === metric)?.rankLabel}`}
              items={ranked}
              color={metric === "watch_hours" ? CHART.blue : metric === "engagement" ? CHART.amber : CHART.violet}
              format={METRICS.find((m) => m.value === metric)?.format}
              action={<Segmented value={metric} onChange={setMetric} options={METRICS} />}
            />

            <PieChart
              title="Engagement mix"
              subtitle="Events by engagement band · chat, questions and reactions against peak audience"
              data={engagementMix}
              colors={[CHART.emerald, CHART.amber, CHART.rose]}
              empty={engagementMix.length === 0}
            />
          </div>

          {/* Did the audience stay? */}
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <ChartCard
              title="Audience retention"
              subtitle="Watch hours against peak viewers · one point per event"
              height={260}
              empty={retention.length === 0}
              emptyText="No events with an audience yet."
            >
              <div style={{ height: 260 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <ScatterChart margin={{ left: -12, right: 8, top: 8, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-slate-100 dark:text-slate-800" />
                    <XAxis type="number" dataKey="x" name="Peak viewers" fontSize={11} tickLine={false} axisLine={false} stroke="currentColor" className="text-slate-400 dark:text-slate-500" />
                    <YAxis type="number" dataKey="y" name="Watch hours" unit="h" fontSize={11} tickLine={false} axisLine={false} stroke="currentColor" className="text-slate-400 dark:text-slate-500" width={44} />
                    <ChartTooltip
                      cursor={{ strokeDasharray: "3 3" }}
                      formatter={(v, name) => [name === "Watch hours" ? `${v} hrs` : v.toLocaleString(), name]}
                      labelFormatter={() => ""}
                    />
                    <Scatter data={retention} name="Events" fill={CHART.violet} fillOpacity={0.75} />
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
              <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">
                Up and to the right is a big audience that stayed. Far right but low means they
                arrived and left.
              </p>
            </ChartCard>

            <Card padding="md" className="flex flex-col items-start gap-2">
              <h2 className="font-semibold text-slate-900 dark:text-white">Device, location & traffic-source breakdowns</h2>
              <p className="flex items-start gap-2 text-sm text-slate-500 dark:text-slate-400">
                <FiInfo className="mt-0.5 shrink-0" />
                {data.breakdowns_note}
              </p>
            </Card>
          </div>

          {/* Recent reports */}
          <Card padding="none" className="overflow-hidden">
            <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
              <div>
                <h2 className="font-semibold text-slate-900 dark:text-white">Recent Reports</h2>
                <p className="text-sm text-slate-500 dark:text-slate-400">Per-event performance summary</p>
              </div>
              <button
                onClick={exportReport}
                className="hidden items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 transition hover:bg-slate-50 sm:inline-flex dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                <FiDownload /> Export CSV
              </button>
            </div>
            {reports.length === 0 ? (
              <p className="px-5 py-10 text-center text-sm text-slate-400 dark:text-slate-500">No events in this window yet.</p>
            ) : (
              <DataTable
                columns={reportColumns}
                rows={reports}
                rowKey={(r) => r.id}
                minWidth={720}
                searchable
                searchKeys={["event"]}
                searchPlaceholder="Find an event…"
                initialSort={{ key: "viewers", dir: "desc" }}
                pageSize={10}
              />
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
