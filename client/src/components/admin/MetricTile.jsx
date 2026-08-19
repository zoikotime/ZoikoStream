import { Link } from "react-router-dom";
import { FiTriangle } from "react-icons/fi";
import { CONSOLE, cx, type } from "../../ui/tokens";
import { Sparkline } from "../../ui/charts";

// The console KPI tile, shared by the Super Admin Command Center and the Organization
// Overview. `value` arrives pre-formatted because every metric owns its unit; the tile
// owns layout, the delta badge, the chart slot and the footer link.
//
// A tile whose metric has no source renders an em dash plus the reason — never a plausible
// number. Both dashboards are read as ground truth during an incident.
export default function MetricTile({
  label,
  value,
  unit,
  delta,
  up,
  note,
  series,
  color,
  to,
  linkLabel,
  age,
  tone,
  className = "",
}) {
  const hasValue = value != null && value !== "";
  return (
    // `relative` pairs with the stretched footer link below: the whole tile becomes the click
    // target without nesting a second <a>, which is invalid and breaks keyboard order.
    <div
      className={cx(
        CONSOLE.panel,
        CONSOLE.panelHover,
        "relative flex flex-col p-4 transition duration-150 motion-reduce:transition-none",
        to && "hover:-translate-y-0.5 hover:shadow-md hover:shadow-slate-900/5 motion-reduce:hover:translate-y-0 dark:hover:shadow-black/30",
        className
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className={cx("text-[13px] font-medium leading-tight", CONSOLE.muted)}>{label}</p>
        {delta != null && (
          <span
            className={cx(
              "inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-bold",
              up
                ? "bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400"
                : "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400"
            )}
          >
            <FiTriangle
              className={cx("text-[7px]", up ? "" : "rotate-180")}
              style={{ fill: "currentColor" }}
              aria-hidden="true"
            />
            {delta}
          </span>
        )}
      </div>

      <p className="mt-2 flex items-baseline gap-1">
        <span
          className={cx(
            "text-[30px] font-semibold leading-none tracking-tight tabular-nums",
            hasValue ? tone || CONSOLE.heading : CONSOLE.faint
          )}
        >
          {hasValue ? value : "—"}
        </span>
        {unit && hasValue && <span className={cx("text-[13px] font-medium", CONSOLE.faint)}>{unit}</span>}
      </p>

      {note && <p className={cx("mt-1.5 text-[11px] leading-snug", CONSOLE.faint)}>{note}</p>}

      {/* Chart slot. Under two samples there is nothing to draw, so it holds a faint
          baseline — the tile keeps its height and reads as "no history yet". */}
      <div className="mt-auto pt-3">
        {series?.length > 1 ? (
          <Sparkline data={series} color={color} height={34} />
        ) : (
          <div className="flex h-[34px] items-end" aria-hidden="true">
            <span
              className="h-px w-full rounded-full bg-slate-200 dark:bg-white/10"
              title="No history for this window yet"
            />
          </div>
        )}
      </div>

      {to && (
        <div className={cx("mt-2 flex items-center justify-between border-t pt-2", CONSOLE.divider)}>
          <Link
            to={to}
            className={cx(
              "text-[12px] font-semibold after:absolute after:inset-0 after:rounded-xl after:content-['']",
              CONSOLE.link
            )}
          >
            {linkLabel} →
          </Link>
          {age != null && <span className={cx("text-[11px]", type.mono, CONSOLE.faint)}>{age}</span>}
        </div>
      )}
    </div>
  );
}
