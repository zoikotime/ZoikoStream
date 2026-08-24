import { Link } from "react-router-dom";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";

// Icon-led metric card: icon tile, label, figure, supporting note.
//
// Distinct from its two neighbours rather than a third copy of them:
//   MetricTile — figure + delta + sparkline + footer link (needs a history series)
//   KpiCard    — a PRESSABLE figure that filters the table below it
//   MetricCard — an icon-led figure for inventories with no series to draw
// Use this where a sparkline would be an empty baseline stub and the metric is a count.
//
// A null `value` renders an em dash with `reason` on hover, never a 0 — the same
// convention StatRow and MetricTile follow, because a fabricated zero reads as a
// measured clean result during an incident.
//
// Accents are declared here rather than pulled from tokens' ACCENT map because index.css
// remaps emerald→violet, so ACCENT.emerald cannot express "green = healthy". These use
// the literal green/amber/rose scales, matching HealthDot and TONE.
const ACCENTS = {
  violet: {
    chip: "bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400",
    border: "hover:border-violet-300 dark:hover:border-violet-500/40",
    glow: "from-violet-500/10",
  },
  green: {
    chip: "bg-green-100 text-green-600 dark:bg-green-500/15 dark:text-green-400",
    border: "hover:border-green-300 dark:hover:border-green-500/40",
    glow: "from-green-500/10",
  },
  amber: {
    chip: "bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400",
    border: "hover:border-amber-300 dark:hover:border-amber-500/40",
    glow: "from-amber-500/10",
  },
  blue: {
    chip: "bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400",
    border: "hover:border-blue-300 dark:hover:border-blue-500/40",
    glow: "from-blue-500/10",
  },
  rose: {
    chip: "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400",
    border: "hover:border-rose-300 dark:hover:border-rose-500/40",
    glow: "from-rose-500/10",
  },
  slate: {
    chip: "bg-slate-100 text-slate-500 dark:bg-white/[0.07] dark:text-neutral-400",
    border: "hover:border-slate-300 dark:hover:border-white/20",
    glow: "from-slate-500/10",
  },
};

export default function MetricCard({
  icon: Icon,
  label,
  value,
  note,
  reason,
  accent = "violet",
  loading = false,
  to,
  className = "",
}) {
  const a = ACCENTS[accent] || ACCENTS.violet;
  const missing = value == null || value === "";
  const Tag = to ? Link : "div";

  return (
    <Tag
      {...(to ? { to } : {})}
      className={cx(
        CONSOLE.panel,
        "group relative isolate flex flex-col overflow-hidden p-4",
        "transition-[border-color,box-shadow,transform] duration-200 ease-out",
        "motion-reduce:transition-none",
        a.border,
        to && cx("cursor-pointer hover:-translate-y-0.5 hover:shadow-lg hover:shadow-slate-900/5 dark:hover:shadow-black/40", focusRing),
        !to && "hover:shadow-sm",
        className
      )}
    >
      {/* Accent bloom in the top-right, revealed on hover. Sits behind content via
          isolate/-z-10 so it can never intercept a click. */}
      <span
        aria-hidden="true"
        className={cx(
          "pointer-events-none absolute -right-6 -top-6 -z-10 h-24 w-24 rounded-full bg-gradient-to-br to-transparent opacity-0 blur-2xl",
          "transition-opacity duration-200 group-hover:opacity-100 motion-reduce:transition-none",
          a.glow
        )}
      />

      <div className="flex items-start justify-between gap-3">
        {Icon && (
          <span
            className={cx(
              "grid h-9 w-9 shrink-0 place-items-center rounded-lg",
              "transition-transform duration-200 ease-out group-hover:scale-105 motion-reduce:transition-none motion-reduce:group-hover:scale-100",
              a.chip
            )}
            aria-hidden="true"
          >
            <Icon className="text-[17px]" />
          </span>
        )}
      </div>

      <p className={cx("mt-3 text-[13px] font-medium leading-tight", CONSOLE.muted)}>{label}</p>

      {loading ? (
        <div className="mt-2 h-8 w-16 rounded bg-slate-200 zk-skeleton dark:bg-white/[0.07]" />
      ) : (
        <p
          className={cx(type.stat, "mt-1", missing ? CONSOLE.faint : CONSOLE.heading)}
          title={missing ? reason : undefined}
        >
          {missing ? "—" : value}
        </p>
      )}

      {note && <p className={cx("mt-1.5 text-[11px] leading-snug", CONSOLE.faint)}>{note}</p>}
    </Tag>
  );
}
