import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { cx, focusRing } from "../../../ui/tokens";
import Skeleton from "../../../ui/Skeleton";
import { BAR, CARD, CARD_INTERACTIVE, CHIP, TXT } from "./styles";
import { item, liftHover, liftTap } from "./motion";

// One tile in the Organization Overview strip: icon + label on one line, the figure
// below it, then a caption.
//
// `note` exists because several figures this platform does not measure per-organization
// (request volume, for one). A tile with a real number renders it; a tile without one
// renders the reason in the footer instead of a plausible-looking zero.
const SUB_TONE = {
  muted: TXT.muted,
  emerald: "text-emerald-600 dark:text-emerald-400",
  amber: "text-amber-600 dark:text-amber-400",
  rose: "text-rose-600 dark:text-rose-400",
};

export default function MetricCard({
  icon: Icon,
  label,
  value,
  unit,
  sub,
  subTone = "muted",
  percent = null,
  // What the bar is a percentage OF. Quota tiles are "used"; the posture tile is scored
  // out of a maximum, and captioning that "used" would misread the number.
  percentLabel = "used",
  tone = "violet",
  badge,
  note,
  to,
  loading = false,
}) {
  const body = (
    <div className="flex h-full flex-col p-5">
      <div className="flex items-center gap-2.5">
        <span className={cx("grid h-8 w-8 shrink-0 place-items-center rounded-lg", CHIP[tone] || CHIP.violet)}>
          <Icon className="h-4 w-4" aria-hidden="true" />
        </span>
        <span className={cx("min-w-0 flex-1 truncate text-[13px] font-semibold", TXT.body)}>{label}</span>
        {badge}
        {to && (
          <ArrowUpRight
            className={cx(
              "h-4 w-4 shrink-0 opacity-0 transition-all duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:opacity-100",
              TXT.faint
            )}
            aria-hidden="true"
          />
        )}
      </div>

      {loading ? (
        <Skeleton className="mt-4 h-8 w-20" />
      ) : (
        <div className="mt-4 flex flex-wrap items-baseline gap-x-1.5">
          <span className={cx("text-[28px] font-bold leading-8 tracking-tight tabular-nums", TXT.heading)}>
            {value}
          </span>
          {unit && <span className={cx("text-sm font-medium", TXT.muted)}>{unit}</span>}
        </div>
      )}

      {loading ? (
        <Skeleton className="mt-2 h-3 w-28" />
      ) : (
        sub && (
          <p className={cx("mt-1.5 text-[12px] font-medium leading-5", SUB_TONE[subTone] || TXT.muted)}>{sub}</p>
        )
      )}

      {/* Quota bar. Only drawn when there is a real ceiling to measure against — a plan
          without a limit gets no bar rather than a bar against an invented maximum. */}
      {!loading && percent != null && (
        <div className="mt-3">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-white/[0.07]">
            <motion.div
              className={cx("h-full rounded-full", BAR[tone] || BAR.violet)}
              initial={{ width: 0 }}
              animate={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
              transition={{ duration: 0.8, ease: [0.22, 0.61, 0.36, 1], delay: 0.15 }}
            />
          </div>
          <p className={cx("mt-1.5 text-[11px] font-medium tabular-nums", TXT.faint)}>
            {percent.toFixed(percent < 10 ? 1 : 0)}% {percentLabel}
          </p>
        </div>
      )}

      {note && <p className={cx("mt-auto pt-4 text-[11px] leading-4", TXT.faint)}>{note}</p>}
    </div>
  );

  const shell = cx(CARD, CARD_INTERACTIVE, "group h-full", to && focusRing);

  return (
    <motion.div variants={item} whileHover={to ? liftHover : undefined} whileTap={to ? liftTap : undefined}>
      {to ? (
        // No aria-label: the card's own text (label, value, sub) is what a screen reader
        // should announce — a label here would replace it with three words.
        <Link to={to} className={cx(shell, "block")}>
          {body}
        </Link>
      ) : (
        <div className={shell}>{body}</div>
      )}
    </motion.div>
  );
}
