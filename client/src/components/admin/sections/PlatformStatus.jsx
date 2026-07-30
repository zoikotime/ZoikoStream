import dayjs from "dayjs";
import HealthDot from "../HealthDot";
import StatStrip from "../StatStrip";
import { compact, money, seriesDelta } from "../format";

const VERDICT = {
  ok: { status: "ok", label: "All systems operational" },
  warn: { status: "warn", label: "Minor degradation" },
  down: { status: "down", label: "Service disruption" },
};

// Section 1 — Platform Status. The page's single opening verdict plus a compact
// stat strip. `summary` is /admin/dashboard's summary block; growth deltas come
// from the last two points of the monthly growth series (null when there isn't
// enough history yet, rendered as no delta rather than a fabricated one).
export default function PlatformStatus({ summary, organizationGrowth, userGrowth }) {
  const verdict = VERDICT[summary.platform_health] || VERDICT.ok;
  const orgDelta = seriesDelta(organizationGrowth);
  const userDelta = seriesDelta(userGrowth);

  const items = [
    {
      label: "Organizations",
      value: compact(summary.total_organizations),
      delta: orgDelta ? `${orgDelta.pct}%` : undefined,
      up: orgDelta?.up,
    },
    {
      label: "Total Users",
      value: compact(summary.total_users),
      delta: userDelta ? `${userDelta.pct}%` : undefined,
      up: userDelta?.up,
    },
    { label: "Live Now", value: summary.live_events.toLocaleString(), live: summary.live_events > 0 },
    {
      label: "Concurrent Viewers",
      value: summary.concurrent_viewers != null ? compact(summary.concurrent_viewers) : "—",
    },
    { label: "MRR", value: money(summary.monthly_revenue) },
    { label: "Storage Used", value: `${summary.storage_used_gb.toLocaleString()} GB` },
  ];

  return (
    <div className="space-y-5">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-wider text-violet-600 dark:text-violet-400">
          ZoikoStream · Production
        </p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 sm:text-[28px] dark:text-white">
          Platform Control Center
        </h1>
        <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm text-slate-500 dark:text-slate-400">
          <HealthDot status={verdict.status} pulse badge>{verdict.label}</HealthDot>
          <span className="text-slate-300 dark:text-slate-600">·</span>
          <span>{dayjs().format("dddd, MMMM D, YYYY")}</span>
          <span className="text-slate-300 dark:text-slate-600">·</span>
          <span>monitoring {compact(summary.total_organizations)} organizations</span>
        </div>
      </div>
      <StatStrip items={items} />
    </div>
  );
}
