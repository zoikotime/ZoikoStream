import { FiArrowUpRight, FiArrowDownRight } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Counter from "../../ui/Counter";
import { Sparkline } from "../../ui/charts";

// KPI value + delta + sparkline, laid out for the revenue / analytics strips.
// Distinct from StatsCard (which is icon-led, no sparkline). Reuses Counter + Sparkline.
export default function TrendStat({
  label, value, prefix = "", suffix = "", decimals = 0,
  delta, up = true, data, color = "#8b5cf6", className = "",
}) {
  return (
    <div className={cx("rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900", className)}>
      <p className="text-sm font-medium text-slate-500 dark:text-slate-400">{label}</p>
      <div className="mt-1 flex items-end justify-between gap-3">
        <p className="text-2xl font-bold text-slate-900 dark:text-white">
          <Counter value={value} prefix={prefix} suffix={suffix} decimals={decimals} />
        </p>
        {delta && (
          <span className={cx("mb-1 inline-flex items-center gap-0.5 text-xs font-semibold", up ? "text-green-600 dark:text-green-400" : "text-rose-600 dark:text-rose-400")}>
            {up ? <FiArrowUpRight /> : <FiArrowDownRight />} {delta}
          </span>
        )}
      </div>
      {data && <div className="mt-2"><Sparkline data={data} color={color} height={36} /></div>}
    </div>
  );
}
