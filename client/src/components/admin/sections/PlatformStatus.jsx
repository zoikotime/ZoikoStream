import dayjs from "dayjs";
import HealthDot from "../HealthDot";
import StatStrip from "../StatStrip";
import { alerts, kpis, liveActivity, platformHealth, regions, revenue, services } from "../../../data/platform";
import { compact, money } from "../format";

// One overall verdict, derived from real status signals (regions + services + alerts).
function overall() {
  const down =
    regions.some((r) => r.status === "down") ||
    services.some((s) => s.status === "down") ||
    alerts.some((a) => a.severity === "critical");
  if (down) return { status: "down", label: "Service disruption" };
  const warn =
    regions.some((r) => r.status === "warn") ||
    services.some((s) => s.status === "warn") ||
    alerts.some((a) => a.severity === "warning");
  if (warn) return { status: "warn", label: "Minor degradation" };
  return { status: "ok", label: "All systems operational" };
}

// Section 1 — Platform Status. The page's single opening verdict plus a compact
// stat strip. Replaces the old eight-KPI-card wall.
export default function PlatformStatus() {
  const o = overall();
  const kpi = Object.fromEntries(kpis.map((k) => [k.key, k]));
  const items = [
    { label: "Organizations", value: compact(kpi.orgs.value), delta: kpi.orgs.delta, up: kpi.orgs.up },
    { label: "Total Users", value: compact(kpi.users.value), delta: kpi.users.delta, up: kpi.users.up },
    { label: "Live Now", value: liveActivity.liveEvents.toLocaleString(), live: true },
    { label: "Concurrent Viewers", value: compact(liveActivity.currentViewers), live: true },
    { label: "MRR", value: money(revenue.mrr), delta: `${revenue.growthPct}%`, up: true },
    { label: "Uptime (30d)", value: `${platformHealth.percent}%` },
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
          <HealthDot status={o.status} pulse badge>{o.label}</HealthDot>
          <span className="text-slate-300 dark:text-slate-600">·</span>
          <span>{dayjs().format("dddd, MMMM D, YYYY")}</span>
          <span className="text-slate-300 dark:text-slate-600">·</span>
          <span>monitoring {compact(kpi.orgs.value)} organizations</span>
        </div>
      </div>
      <StatStrip items={items} />
    </div>
  );
}
