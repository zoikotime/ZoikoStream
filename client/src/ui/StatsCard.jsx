import { FiArrowUpRight, FiArrowDownRight } from "react-icons/fi";
import { cx, ACCENT } from "./tokens";
import Card from "./Card";
import Counter from "./Counter";

// KPI / metric card. Numeric `value` animates via Counter; string values render as-is.
// Optional `delta` (with `up`) or `live` pulse. Consolidates the old DashboardCard
// and the homepage metric tiles into one component.
export default function StatsCard({
  title,
  value,
  icon: Icon,
  accent = "emerald",
  suffix = "",
  prefix = "",
  decimals = 0,
  delta,
  up = true,
  live = false,
  className = "",
}) {
  const isNumber = typeof value === "number";
  return (
    <Card hover className={cx("p-5", className)}>
      <div className="flex items-center gap-3">
        {Icon && (
          <span className={cx("grid h-11 w-11 shrink-0 place-items-center rounded-xl", ACCENT[accent].chip)}>
            <Icon className="text-xl" />
          </span>
        )}
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-500 dark:text-slate-400">{title}</p>
          <p className="text-2xl font-bold text-slate-900 dark:text-white">
            {isNumber ? <Counter value={value} prefix={prefix} suffix={suffix} decimals={decimals} /> : value}
          </p>
        </div>
      </div>

      {(delta || live) && (
        <div className="mt-3 flex items-center gap-1 text-xs font-medium">
          {live ? (
            <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" /> Live now
            </span>
          ) : (
            <span className={cx("inline-flex items-center gap-0.5", up ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400")}>
              {up ? <FiArrowUpRight /> : <FiArrowDownRight />} {delta}
              <span className="ml-1 text-slate-400 dark:text-slate-500">vs last week</span>
            </span>
          )}
        </div>
      )}
    </Card>
  );
}
