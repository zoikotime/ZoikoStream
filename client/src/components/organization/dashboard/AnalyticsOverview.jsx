import { FiBarChart2, FiChevronDown } from "react-icons/fi";
import { CONSOLE, cx } from "../../../ui/tokens";
import DashboardPanel from "./DashboardPanel";
import { AreaTrend } from "../../../ui/charts";
import Skeleton from "../../../ui/Skeleton";
import EmptyState from "./EmptyState";
import { RANGES } from "./dashboardConfig";

// Four figures, all straight from analytics().summary — the SAME four the full Analytics
// page shows, under the same labels, so the two screens can never appear to disagree.
//
// "Total Viewers" is that page's own wording for `summary.viewers`, which the service builds
// as the sum of each event's peak audience. Reused deliberately rather than reworded here:
// one number, one name, everywhere it appears.
const METRICS = [
  { key: "viewers", label: "Total Viewers", format: (v) => v.toLocaleString() },
  { key: "watch_hours", label: "Watch Time", format: (v) => `${v.toLocaleString()} hrs` },
  { key: "peak", label: "Peak Concurrent", format: (v) => v.toLocaleString() },
  { key: "engagement", label: "Avg. Engagement", format: (v) => `${v}%` },
];

function MetricsRow({ summary }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-4">
      {METRICS.map((m) => {
        const raw = summary?.[m.key];
        const has = raw != null;
        return (
          <div key={m.key} className="min-w-0">
            <dt className={cx("truncate text-[13px]", CONSOLE.faint)}>{m.label}</dt>
            <dd
              className={cx(
                "mt-1 truncate text-[20px] font-bold tabular-nums",
                has ? CONSOLE.heading : "text-slate-400 dark:text-neutral-500"
              )}
            >
              {has ? m.format(raw) : "—"}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

export default function AnalyticsOverview({ data, loading, error, range, onRange, onRetry }) {
  const summary = data?.summary;
  const series = data?.trends?.viewership || [];
  // A single point cannot draw a trend — it renders as a dot in an empty box, which reads as
  // a broken chart rather than as "one event so far".
  const chartable = series.length > 1;

  const selector = (
    <div className="relative">
      <select
        value={range}
        onChange={(e) => onRange(e.target.value)}
        aria-label="Analytics period"
        className={cx(CONSOLE.select, "appearance-none pr-8 text-[13px]")}
      >
        {RANGES.map((r) => (
          <option key={r.key} value={r.key}>
            {r.label}
          </option>
        ))}
      </select>
      <FiChevronDown
        className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 dark:text-neutral-400"
        aria-hidden="true"
      />
    </div>
  );

  return (
    <DashboardPanel title="Event Analytics Overview" action={selector}>
      {error ? (
        <EmptyState
          icon={FiBarChart2}
          title="Couldn’t load analytics"
          description="The figures for this period are unavailable right now."
          className="py-8"
          action={
            <button
              type="button"
              onClick={onRetry}
              className={cx("text-[12px] font-semibold", CONSOLE.link)}
            >
              Try again
            </button>
          }
        />
      ) : loading && !data ? (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} variant="block" className="h-12" />
            ))}
          </div>
          <Skeleton variant="block" className="h-[200px]" />
        </div>
      ) : (
        <div className="space-y-5">
          <MetricsRow summary={summary} />
          {chartable ? (
            <div>
              <p className={cx("mb-1 text-[11px] font-medium uppercase tracking-wide", CONSOLE.faint)}>
                Viewers over time
              </p>
              <AreaTrend data={series} height={200} showX />
            </div>
          ) : (
            // Never draw a flat line across an empty chart: a straight purple stroke at zero
            // is indistinguishable from real, uneventful data, and it is the one shape a
            // reader will believe. An empty panel that says why is honest and shorter.
            <EmptyState
              icon={FiBarChart2}
              title="No analytics yet"
              description="Viewer analytics will appear after your events receive traffic."
              className="py-6"
            />
          )}
        </div>
      )}
    </DashboardPanel>
  );
}
