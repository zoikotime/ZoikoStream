import { FiArrowDownRight, FiArrowUpRight } from "react-icons/fi";
import Counter from "../../ui/Counter";
import { Sparkline } from "../../ui/charts";
import { SERIES, cx, panelSurface, t150, type } from "../../ui/tokens";

// Metric tile: label + count-up value + trend delta (color + icon) + optional sparkline.
// The delta + sparkline are what make the dashboard feel "alive". Renders a
// shape-matched skeleton while loading (not a spinner).
export default function StatCard({
  label,
  value,
  prefix = "",
  suffix = "",
  decimals = 0,
  delta,
  up = true,
  trend,
  color = SERIES.brand,
  loading = false,
  className = "",
  // Optional one-line qualifier under the figure, for a count whose meaning depends on
  // context the number cannot carry — e.g. "0 at capacity" reads differently when no event
  // has a capacity configured at all. Additive and guarded: every existing caller omits it
  // and renders exactly as before. Mirrors the same prop on ui/StatsCard.
  hint,
}) {
  if (loading) {
    return (
      <div className={cx(panelSurface, "p-4", className)} aria-hidden="true">
        <div className="zk-skeleton h-3 w-20 rounded bg-slate-200 dark:bg-slate-800" />
        <div className="zk-skeleton mt-2 h-7 w-28 rounded bg-slate-200 dark:bg-slate-800" />
        {trend !== undefined && <div className="zk-skeleton mt-3 h-9 w-full rounded bg-slate-200 dark:bg-slate-800" />}
      </div>
    );
  }

  return (
    <div className={cx(panelSurface, t150, "group p-4 hover:border-slate-300 dark:hover:border-slate-700", className)}>
      <p className={cx(type.label, "font-medium text-slate-500 dark:text-slate-400")}>{label}</p>
      <div className="mt-1 flex items-end justify-between gap-3">
        <p className={cx(type.stat, "text-slate-900 dark:text-white")}>
          {typeof value === "number" ? (
            <Counter value={value} prefix={prefix} suffix={suffix} decimals={decimals} />
          ) : (
            value
          )}
        </p>
        {delta != null && (
          <span
            className={cx(
              "mb-1.5 inline-flex items-center gap-0.5 text-xs font-semibold",
              up ? "text-green-600 dark:text-green-400" : "text-rose-600 dark:text-rose-400"
            )}
          >
            {up ? <FiArrowUpRight /> : <FiArrowDownRight />} {delta}
          </span>
        )}
      </div>
      {hint && (
        <p className="mt-1 text-[11px] leading-4 text-slate-400 dark:text-slate-500">{hint}</p>
      )}
      {trend && (
        <div className="mt-2">
          <Sparkline data={trend} color={color} height={36} />
        </div>
      )}
    </div>
  );
}
