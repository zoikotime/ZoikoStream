import { FiArrowUpRight, FiArrowDownRight } from "react-icons/fi";
import { cx } from "../../ui/tokens";

// Horizontal KPI strip — inline metrics separated by hairlines, tabular numerals.
// Replaces the grid-of-KPI-cards. Hairlines come from a 1px grid gap over a slate
// background (robust across wrapping — no fiddly per-cell border math).
//
// items: [{ label, value, delta, up, live }]  (value is preformatted text/node)
export default function StatStrip({ items, className = "" }) {
  return (
    <div
      className={cx(
        "overflow-hidden rounded-xl border border-slate-200 bg-slate-200 dark:border-slate-800 dark:bg-slate-800",
        className
      )}
    >
      <div className="grid grid-cols-2 gap-px sm:grid-cols-3 lg:grid-cols-6">
        {items.map((s) => (
          <div key={s.label} className="bg-white px-5 py-4 dark:bg-slate-900">
            <p className="truncate text-xs font-medium text-slate-500 dark:text-slate-400">{s.label}</p>
            <p className="mt-1 text-[22px] font-semibold tabular-nums tracking-tight text-slate-900 dark:text-white">
              {s.value}
            </p>
            {(s.delta || s.live) && (
              <p className="mt-1 flex items-center gap-1 text-[11px] font-medium">
                {s.live ? (
                  <span className="inline-flex items-center gap-1 text-green-600 dark:text-green-400">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-green-500" /> Live
                  </span>
                ) : (
                  <>
                    <span
                      className={cx(
                        "inline-flex items-center gap-0.5",
                        s.up ? "text-green-600 dark:text-green-400" : "text-rose-600 dark:text-rose-400"
                      )}
                    >
                      {s.up ? <FiArrowUpRight /> : <FiArrowDownRight />} {s.delta}
                    </span>
                    <span className="text-slate-400 dark:text-slate-500">7d</span>
                  </>
                )}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
