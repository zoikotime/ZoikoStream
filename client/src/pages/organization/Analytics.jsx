// client/src/pages/organization/Analytics.jsx
// Analytics Dashboard — event performance for an organization.
// Route: /organization/analytics. Rendered inside OrganizationLayout.
// Backed by GET /organization/analytics (services/org.py analytics()) — real numbers from
// Event + BroadcastSession + AnalyticsSnapshot. Device/location/traffic-source breakdowns
// aren't shown here: nothing in this stack persists them historically (see the endpoint's
// own `breakdowns_note`), so a bar chart for them would be invented, not measured.
import { useMemo, useState } from "react";
import { FiCalendar, FiChevronDown, FiDownload, FiUsers, FiClock, FiTrendingUp, FiActivity, FiInfo } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import api from "../../api";
import useApi from "../../hooks/useApi";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import StatsCard from "../../ui/StatsCard";
import Spinner from "../../ui/Spinner";
import { notify } from "../../ui/Toast";
import { AreaChart, BarChart } from "../../ui/charts";
import DataTable from "../../components/admin/DataTable";
import { fmtDate } from "../../data/events";

const CHART = { violet: "#7c3aed", blue: "#3b82f6", emerald: "#10b981", amber: "#f59e0b", rose: "#f43f5e" };

const RANGES = [
  { key: "7d", label: "Last 7 days" },
  { key: "30d", label: "Last 30 days" },
  { key: "90d", label: "Last 90 days" },
  { key: "12m", label: "Last 12 months" },
];

const control =
  "rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 shadow-sm outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

const engTone = (v) =>
  v >= 70 ? CHART.emerald : v >= 55 ? CHART.amber : CHART.rose;

const reportColumns = [
  { key: "event", header: "Event", className: "whitespace-nowrap", render: (r) => <span className="font-medium text-slate-800 dark:text-slate-100">{r.event}</span> },
  { key: "date", header: "Date", className: "whitespace-nowrap", render: (r) => (r.date ? fmtDate(r.date) : "—") },
  { key: "viewers", header: "Peak Viewers", align: "right", className: "whitespace-nowrap", render: (r) => r.viewers.toLocaleString() },
  { key: "watch_hours", header: "Watch Time", align: "right", className: "whitespace-nowrap", render: (r) => `${r.watch_hours.toLocaleString()} hrs` },
  { key: "engagement", header: "Engagement", className: "whitespace-nowrap", render: (r) => (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div className="h-full rounded-full" style={{ width: `${r.engagement}%`, background: engTone(r.engagement) }} />
      </div>
      <span className="tabular-nums font-medium text-slate-700 dark:text-slate-200">{r.engagement}%</span>
    </div>
  ) },
];

function RankedBars({ title, subtitle, items, color, format }) {
  const max = Math.max(...items.map((i) => i.value)) || 1;
  return (
    <Card padding="md">
      <div className="mb-4">
        <h2 className="font-semibold text-slate-900 dark:text-white">{title}</h2>
        {subtitle && <p className="text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
      </div>
      {items.length === 0 ? (
        <p className="py-6 text-center text-sm text-slate-400 dark:text-slate-500">No events with viewers in this window yet.</p>
      ) : (
        <ul className="space-y-3.5">
          {items.map((i) => (
            <li key={i.label}>
              <div className="mb-1 flex items-center justify-between gap-2 text-sm">
                <span className="truncate pr-2 text-slate-700 dark:text-slate-200">{i.label}</span>
                <span className="shrink-0 font-medium tabular-nums text-slate-500 dark:text-slate-400">{format(i.value)}</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                <div className="h-full rounded-full transition-all" style={{ width: `${(i.value / max) * 100}%`, background: color }} />
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
  const { data, loading, error } = useApi(() =>
    api.get("/organization/analytics", { params: { range } }).then((r) => r.data)
  );
  const rangeLabel = RANGES.find((r) => r.key === range)?.label ?? "";

  const kpis = useMemo(() => {
    const s = data?.summary || { viewers: 0, watch_hours: 0, peak: 0, engagement: 0 };
    return [
      { title: "Total Viewers", value: s.viewers, icon: FiUsers, accent: "violet" },
      { title: "Watch Time", value: s.watch_hours, suffix: " hrs", decimals: 1, icon: FiClock, accent: "blue" },
      { title: "Peak Concurrent Viewers", value: s.peak, icon: FiTrendingUp, accent: "emerald" },
      { title: "Avg. Engagement", value: s.engagement, suffix: "%", icon: FiActivity, accent: "amber" },
    ];
  }, [data]);

  const reports = data?.reports || [];

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

        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative">
            <FiCalendar className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <select
              value={range}
              onChange={(e) => setRange(e.target.value)}
              aria-label="Date range"
              className={cx(control, "appearance-none pl-9 pr-8")}
            >
              {RANGES.map((r) => (
                <option key={r.key} value={r.key}>{r.label}</option>
              ))}
            </select>
            <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          </div>
          <Button size="sm" onClick={exportReport} disabled={!reports.length}>
            <FiDownload className="text-base" /> Export Report
          </Button>
        </div>
      </div>

      {error ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          Couldn't load analytics. Try refreshing the page.
        </div>
      ) : loading ? (
        <div className="grid place-items-center py-20"><Spinner /></div>
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {kpis.map((k) => (
              <StatsCard key={k.title} {...k} />
            ))}
          </div>

          {/* Charts */}
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <AreaChart
              title="Viewership"
              subtitle={`Peak viewers per event · ${rangeLabel}`}
              data={data.trends.viewership}
              keys={[{ key: "value", name: "Viewers", color: CHART.violet }]}
              type="area"
            />
            <BarChart title="Watch Time" subtitle={`Hours watched · ${rangeLabel}`} data={data.trends.watch_time} color={CHART.blue} />
          </div>

          {/* Additional analytics */}
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <RankedBars title="Top Performing Events" subtitle="By peak viewers" items={data.top_events} color={CHART.violet} format={(v) => v.toLocaleString()} />
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
              <DataTable columns={reportColumns} rows={reports} rowKey={(r) => r.id} minWidth={720} />
            )}
          </Card>
        </>
      )}
    </div>
  );
}
