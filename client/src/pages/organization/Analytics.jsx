// client/src/pages/organization/Analytics.jsx
// Analytics Dashboard — event performance for an organization.
// Route: /organization/analytics. Rendered inside OrganizationLayout.
// No backend: the date range drives local state; Export builds a CSV client-side.
import { useMemo, useState } from "react";
import { FiCalendar, FiChevronDown, FiDownload, FiUsers, FiClock, FiTrendingUp, FiActivity } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import StatsCard from "../../ui/StatsCard";
import { notify } from "../../ui/Toast";
import BarChartCard from "../../components/Dashboard/BarChartCard";
import AreaChartCard from "../../components/Dashboard/AreaChartCard";
import DonutChartCard from "../../components/Dashboard/DonutChartCard";
import { fmtDate } from "../../data/events";
import {
  CHART, CATEGORICAL, RANGES, rangeLabel, summary, trends,
  retention, topEvents, locations, devices, trafficSources, reports,
} from "../../data/analytics";

const control =
  "rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 shadow-sm outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

const th = "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-400 whitespace-nowrap";
const td = "px-4 py-3 text-sm text-slate-600 dark:text-slate-300 whitespace-nowrap";

const engTone = (v) =>
  v >= 70 ? CHART.emerald : v >= 55 ? CHART.amber : CHART.rose;

// Horizontal ranked bars — reused for Top Events and Viewer Locations.
function RankedBars({ title, subtitle, items, color, format }) {
  const max = Math.max(...items.map((i) => i.value)) || 1;
  return (
    <Card padding="md">
      <div className="mb-4">
        <h2 className="font-semibold text-slate-900 dark:text-white">{title}</h2>
        {subtitle && <p className="text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
      </div>
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
    </Card>
  );
}

export default function OrganizationAnalytics() {
  const [range, setRange] = useState("30d");
  const factor = RANGES.find((r) => r.key === range)?.factor ?? 1;

  const s = useMemo(() => summary(factor), [factor]);
  const t = useMemo(() => trends(range), [range]);
  const label = rangeLabel(range);

  const kpis = [
    { title: "Total Viewers", value: s.viewers, icon: FiUsers, accent: "violet", delta: "12%", up: true },
    { title: "Watch Time", value: s.watchHours, suffix: " hrs", icon: FiClock, accent: "blue", delta: "9%", up: true },
    { title: "Peak Concurrent Viewers", value: s.peak, icon: FiTrendingUp, accent: "emerald", delta: "5%", up: true },
    { title: "Avg. Engagement", value: s.engagement, suffix: "%", icon: FiActivity, accent: "amber", delta: "4%", up: true },
  ];

  const exportReport = () => {
    const head = ["Event", "Date", "Total Viewers", "Watch Time (hrs)", "Engagement (%)"];
    const body = reports.map((r) => [r.event, r.date, r.viewers, r.watchHours, r.engagement]);
    const csv = [head, ...body]
      .map((row) => row.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(","))
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
          <Button size="sm" onClick={exportReport}>
            <FiDownload className="text-base" /> Export Report
          </Button>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((k) => (
          <StatsCard key={k.title} {...k} />
        ))}
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <AreaChartCard
          title="Viewership Growth"
          subtitle={`Viewers · ${label}`}
          data={t.viewership}
          keys={[{ key: "value", name: "Viewers", color: CHART.violet }]}
          type="area"
        />
        <AreaChartCard
          title="Attendance Trend"
          subtitle="Registered vs. attended"
          data={t.attendance}
          keys={[
            { key: "registered", name: "Registered", color: CHART.blue },
            { key: "attended", name: "Attended", color: CHART.emerald },
          ]}
          type="line"
        />
        <BarChartCard title="Watch Time" subtitle={`Hours watched · ${label}`} data={t.watchTime} color={CHART.violet} />
        <AreaChartCard
          title="Audience Retention"
          subtitle="% of audience still watching"
          data={retention}
          keys={[{ key: "value", name: "Retention", color: CHART.rose }]}
          type="area"
          suffix="%"
        />
      </div>

      {/* Additional analytics */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <RankedBars title="Top Performing Events" subtitle="By total viewers" items={topEvents} color={CHART.violet} format={(v) => v.toLocaleString()} />
        <RankedBars title="Viewer Locations" subtitle="Share of total viewers" items={locations} color={CHART.blue} format={(v) => `${v}%`} />
        <DonutChartCard title="Devices Used" subtitle="How viewers tuned in" data={devices} colors={CATEGORICAL} />
        <DonutChartCard title="Traffic Sources" subtitle="Where viewers came from" data={trafficSources} colors={CATEGORICAL} />
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
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className={th}>Event</th>
                <th className={th}>Date</th>
                <th className={`${th} text-right`}>Total Viewers</th>
                <th className={`${th} text-right`}>Watch Time</th>
                <th className={th}>Engagement</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {reports.map((r) => (
                <tr key={r.id} className="transition hover:bg-slate-50 dark:hover:bg-slate-800/50">
                  <td className={cx(td, "font-medium text-slate-800 dark:text-slate-100")}>{r.event}</td>
                  <td className={td}>{fmtDate(r.date)}</td>
                  <td className={cx(td, "text-right tabular-nums")}>{r.viewers.toLocaleString()}</td>
                  <td className={cx(td, "text-right tabular-nums")}>{r.watchHours.toLocaleString()} hrs</td>
                  <td className={td}>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                        <div className="h-full rounded-full" style={{ width: `${r.engagement}%`, background: engTone(r.engagement) }} />
                      </div>
                      <span className="tabular-nums font-medium text-slate-700 dark:text-slate-200">{r.engagement}%</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
